import discord
from discord.ext import commands
from datetime import timedelta
from collections import defaultdict
import re
import time
import asyncio
from dotenv import load_dotenv
import os
from keep_alive import keep_alive
from deep_translator import GoogleTranslator
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ========== CẤU HÌNH TOÀN CỤC ==========
OWNER_ID = 1075422580833529879

# Ngưỡng mặc định
SPAM_THRESHOLD = 5
SPAM_WINDOW = 10
EMOJI_SPAM_LIMIT = 5
STICKER_SPAM_LIMIT = 10
STICKER_WINDOW = 5
RAID_THRESHOLD = 10
RAID_WINDOW = 5
MAX_LINE_LENGTH = 50

# Giới hạn spam ảnh/video/gif
MEDIA_LIMIT = 5
MEDIA_WINDOW = 10

BAD_WORDS = ["địt","lồn","cặc","đụ","vcl","dm","đmm","cc","cac","duma","ditme","fuck","shit","bitch","đĩ","mẹ kiếp","chết tiệt","mẹ mày","nigga","nigger","nick her"]
SCAM_DOMAINS = ["bit.ly","tinyurl.com","rebrand.ly","discord.gift","steamcommmunity.com","nitro-steam.com","free-discord-nitro.com","trúng thưởng","quà tặng","free nitro","giveaway","mrbeast","beast games"]
SCAM_IMAGE_KEYWORDS = ["mrbeast","mr beast","jj","j.j","giveaway","quà tặng","free nitro","free steam","free gift","trúng thưởng","nhận quà","100% free"]
NSFW_KEYWORDS = ["nsfw","18+","porn","sex","adult","xxx","hentai","dirty","khiêu dâm","người lớn","18plus","sexviet","vlxx","phim sex"]
INVITE_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?discord(?:app)?\.(?:com|gg)/(?:invite/)?([a-zA-Z0-9\-]+)", re.IGNORECASE)

# Cấu hình cho từng server (key = guild.id)
guild_config = defaultdict(lambda: {
    "ANTI_NUKE": True,
    "ANTI_SPAM": True,
    "ANTI_RAID": True,
    "ALLOWED_ROLE_ID": None,
    "ALLOWED_USERS": [],
    "ALLOWED_BAD_WORDS": [],
    "ALLOWED_BAD_MEMBERS": [],
    "ALLOWED_CHANNELS": [],          # kênh được miễn spam (vẫn check scam/nsfw)
    "TRANSLATE_CHANNELS": [],        # chỉ dịch trong các kênh này
    "ALLOWED_ROLES": [],
    "log_channel_id": None,
    "RAID_MODE": False,
    "join_times": [],
    "user_messages": defaultdict(list),
    "user_stickers": defaultdict(list),
    "user_media": defaultdict(list),  # lưu thời gian gửi media
    "violation_count": defaultdict(int),
    "blacklist": []                   # danh sách user bị giám sát đặc biệt
})

# Quản lý solo task (toàn cục theo channel)
solo_tasks = {}

# ========== HÀM KIỂM TRA QUYỀN ==========
async def check_admin_permission(ctx):
    gcfg = guild_config[ctx.guild.id]
    if ctx.author.id == OWNER_ID:
        return True
    if ctx.author.id == ctx.guild.owner_id:
        return True
    if ctx.author.id in gcfg["ALLOWED_USERS"]:
        return True
    if gcfg["ALLOWED_ROLE_ID"] is not None:
        role = ctx.guild.get_role(gcfg["ALLOWED_ROLE_ID"])
        if role and role in ctx.author.roles:
            return True
    await ctx.send("❌ Bạn không có quyền dùng lệnh này!")
    return False

# ========== HÀM LOG ==========
async def log_action(guild, action, target, moderator=None, reason=None):
    gcfg = guild_config[guild.id]
    if not gcfg["log_channel_id"]:
        return
    channel = bot.get_channel(gcfg["log_channel_id"])
    if not channel:
        return
    embed = discord.Embed(title=f"🛡️ {action}", color=discord.Color.blue())
    embed.add_field(name="Người dùng", value=target, inline=False)
    if moderator:
        embed.add_field(name="Người thực thi", value=moderator, inline=False)
    if reason:
        embed.add_field(name="Lý do", value=reason, inline=False)
    embed.timestamp = discord.utils.utcnow()
    await channel.send(embed=embed)

# ========== HÀM KIỂM TRA NỘI DUNG ==========
def count_emojis(content):
    unicode_pattern = re.compile(r'[\U00010000-\U0010FFFF]', flags=re.UNICODE)
    unicode_count = len(unicode_pattern.findall(content))
    custom_pattern = re.compile(r'<a?:\w+:\d+>')
    custom_count = len(custom_pattern.findall(content))
    return unicode_count + custom_count

def contains_bad_words(content, author_id, guild_id):
    gcfg = guild_config[guild_id]
    if author_id in gcfg["ALLOWED_BAD_MEMBERS"]:
        return False
    c = content.lower()
    for word in BAD_WORDS:
        if word in c:
            if word in gcfg["ALLOWED_BAD_WORDS"]:
                continue
            return True
    return False

def count_lines(content):
    return content.count('\n') + 1 if content else 0

def contains_scam(content):
    """Phát hiện scam từ nội dung và domain"""
    c = content.lower()
    # 1. Domain scam
    for domain in SCAM_DOMAINS:
        if domain in c:
            return True
    # 2. Từ khóa scam ảnh
    for kw in SCAM_IMAGE_KEYWORDS:
        if kw in c:
            return True
    return False

def contains_nsfw(content):
    c = content.lower()
    urls = re.findall(r'https?://\S+', c)
    for url in urls:
        for kw in NSFW_KEYWORDS:
            if kw in url:
                return True
    return False

async def is_allowed_link(ctx, content):
    """Cho phép link trong kênh whitelist? (chỉ áp dụng cho invite, scam vẫn bị chặn)"""
    gcfg = guild_config[ctx.guild.id]
    if ctx.channel.id in gcfg["ALLOWED_CHANNELS"]:
        return True
    return any(role.id in gcfg["ALLOWED_ROLES"] for role in ctx.author.roles)

async def handle_violation(user, guild, reason, is_blacklist=False):
    gcfg = guild_config[guild.id]
    if not is_blacklist:
        gcfg["violation_count"][user.id] += 1
        current = gcfg["violation_count"][user.id]
        await log_action(guild, f"⚠️ Vi phạm lần {current}/5", user.mention, moderator=bot.user, reason=reason)
        if current >= 5:
            try:
                await guild.ban(user, reason=f"Tự động ban sau 5 lần vi phạm: {reason}")
                await log_action(guild, "🔨 ĐÃ BAN", user.mention, moderator=bot.user, reason=f"5 lần vi phạm (lần cuối: {reason})")
                del gcfg["violation_count"][user.id]
                return True
            except:
                pass
    else:
        # blacklist: mỗi lần vi phạm đều mute + đếm (nếu muốn ban sau 5 lần cũng được)
        gcfg["violation_count"][user.id] += 1
        current = gcfg["violation_count"][user.id]
        if current >= 5:
            try:
                await guild.ban(user, reason=f"Blacklist: 5 lần vi phạm")
                await log_action(guild, "🔨 BAN BLACKLIST", user.mention, moderator=bot.user, reason=reason)
            except:
                pass
        else:
            try:
                await user.timeout(timedelta(hours=1), reason=reason)
                await log_action(guild, "🔇 BLACKLIST MUTE", user.mention, moderator=bot.user, reason=reason)
            except:
                pass
    return False

# ========== AUTO DỊCH (CHỈ TRONG KÊNH ĐƯỢC CẤU HÌNH) ==========
translator = GoogleTranslator(source='auto', target='vi')
async def translate_text(text):
    try:
        loop = asyncio.get_event_loop()
        translated = await loop.run_in_executor(None, translator.translate, text)
        return translated
    except Exception as e:
        print(f"Lỗi dịch: {e}")
        return None

def detect_language(text):
    try:
        return detect(text)
    except:
        return None

# ========== SỰ KIỆN ==========
@bot.event
async def on_ready():
    print(f"✅ Bot: {bot.user.name} (ID: {bot.user.id})")
    print(f"👑 Owner bot: {OWNER_ID}")
    print("🛡️ Bot bảo vệ đã sẵn sàng!")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # DM: chỉ xử lý lệnh
    if message.guild is None:
        await bot.process_commands(message)
        return

    gcfg = guild_config[message.guild.id]

    # ========== AUTO DỊCH CHỈ TRONG KÊNH CHO PHÉP ==========
    if message.content and not message.content.startswith('!'):
        # Kiểm tra xem kênh hiện tại có trong danh sách dịch không
        if message.channel.id in gcfg["TRANSLATE_CHANNELS"]:
            if len(message.content.strip()) > 2:
                try:
                    lang = detect_language(message.content)
                    if lang and lang != 'vi':
                        translated = await translate_text(message.content)
                        if translated and translated.lower() != message.content.lower():
                            await message.reply(f"🌐 **Dịch sang tiếng Việt:** {translated}")
                except Exception as e:
                    print(f"Lỗi dịch: {e}")

    # ========== MIỄN TRỪ ADMIN/OWNER ==========
    if message.author.id == OWNER_ID or message.author.guild_permissions.administrator or message.author.id == message.guild.owner_id:
        await bot.process_commands(message)
        return

    ctx = await bot.get_context(message)
    content = message.content
    user = message.author
    guild = message.guild

    # ========== PUNISH FUNCTION ==========
    async def punish(violation_reason, mute_hours=1, is_blacklist=False):
        await message.delete()
        banned = await handle_violation(user, guild, violation_reason, is_blacklist=is_blacklist)
        if not banned:
            try:
                await user.timeout(timedelta(hours=mute_hours), reason=violation_reason)
                await log_action(guild, "🔇 Auto Mute", user.mention, moderator=bot.user, reason=f"{violation_reason} (1 giờ)")
            except:
                pass

    # ========== ANTI SCAM (LUÔN CHẶN) ==========
    if contains_scam(content):
        await punish(f"Scam: {content[:100]}")
        return

    # ========== ANTI NSFW ==========
    if contains_nsfw(content):
        await punish(f"NSFW: {content[:100]}")
        return

    # ========== ANTI INVITE (chặn link mời server khác) ==========
    invite_match = INVITE_PATTERN.search(content)
    if invite_match and not await is_allowed_link(ctx, content):
        await punish("Gửi link mời Discord không được phép")
        return

    # ========== ANTI PING @everyone / @here ==========
    if '@everyone' in content or '@here' in content:
        await punish("Ping @everyone/@here bị cấm", mute_hours=2)
        return

    # ========== KIỂM TRA BLACKLIST ==========
    if user.id in gcfg["blacklist"]:
        await punish(f"Blacklist: {content[:50]}", is_blacklist=True)
        return

    # ========== KÊNH ĐƯỢC ALLOW (miễn spam) ==========
    if ctx.channel.id in gcfg["ALLOWED_CHANNELS"]:
        # Chỉ bỏ qua anti-spam, vẫn kiểm tra scam, nsfw, invite
        await bot.process_commands(message)
        return

    # ========== ANTI SPAM (tin nhắn, emoji, sticker, media) ==========
    if gcfg["ANTI_SPAM"]:
        # 1. Spam emoji
        emoji_count = count_emojis(content)
        if emoji_count > EMOJI_SPAM_LIMIT:
            await punish(f"Spam emoji ({emoji_count})")
            return

        # 2. Spam sticker
        if message.stickers:
            now = time.time()
            gcfg["user_stickers"][user.id].append(now)
            gcfg["user_stickers"][user.id] = [t for t in gcfg["user_stickers"][user.id] if now - t < STICKER_WINDOW]
            if len(gcfg["user_stickers"][user.id]) > STICKER_SPAM_LIMIT:
                await punish(f"Spam sticker ({len(gcfg['user_stickers'][user.id])})")
                return

        # 3. Spam ảnh, video, gif (media)
        if message.attachments or message.embeds:
            now = time.time()
            gcfg["user_media"][user.id].append(now)
            gcfg["user_media"][user.id] = [t for t in gcfg["user_media"][user.id] if now - t < MEDIA_WINDOW]
            if len(gcfg["user_media"][user.id]) > MEDIA_LIMIT:
                await punish(f"Spam media (ảnh/video/gif) {len(gcfg['user_media'][user.id])} lần")
                return

        # 4. Chửi bậy
        if contains_bad_words(content, user.id, guild.id):
            await punish(f"Chửi bậy: {content[:50]}")
            return

        # 5. Tin nhắn quá dài
        if count_lines(content) > MAX_LINE_LENGTH:
            await punish(f"Tin nhắn dài {count_lines(content)} dòng")
            return

        # 6. Spam tần suất
        now = time.time()
        gcfg["user_messages"][user.id].append(now)
        gcfg["user_messages"][user.id] = [t for t in gcfg["user_messages"][user.id] if now - t < SPAM_WINDOW]
        if len(gcfg["user_messages"][user.id]) > SPAM_THRESHOLD:
            await punish(f"Spam tin nhắn ({len(gcfg['user_messages'][user.id])} tin / {SPAM_WINDOW}s)")
            return

    await bot.process_commands(message)

# ========== SỰ KIỆN ANTI-RAID ==========
@bot.event
async def on_member_join(member):
    gcfg = guild_config[member.guild.id]
    if not (gcfg["ANTI_RAID"] and gcfg["ANTI_NUKE"]):
        return
    now = time.time()
    gcfg["join_times"].append(now)
    gcfg["join_times"] = [t for t in gcfg["join_times"] if now - t < RAID_WINDOW]
    if len(gcfg["join_times"]) >= RAID_THRESHOLD:
        gcfg["RAID_MODE"] = True
        await log_action(member.guild, "🚨 KÍCH HOẠT CHẾ ĐỘ RAID", member.mention, moderator=bot.user, reason=f"{len(gcfg['join_times'])} join trong {RAID_WINDOW}s")

@bot.event
async def on_guild_channel_create(channel):
    gcfg = guild_config[channel.guild.id]
    if not (gcfg["ANTI_NUKE"] and gcfg["RAID_MODE"]):
        return
    await channel.delete()
    await log_action(channel.guild, "🧨 Chặn tạo kênh", channel.name, moderator=bot.user, reason="RAID MODE")

@bot.event
async def on_guild_role_create(role):
    gcfg = guild_config[role.guild.id]
    if not (gcfg["ANTI_NUKE"] and gcfg["RAID_MODE"]):
        return
    await role.delete()
    await log_action(role.guild, "🧨 Chặn tạo role", role.name, moderator=bot.user, reason="RAID MODE")

@bot.event
async def on_member_ban(guild, user):
    gcfg = guild_config[guild.id]
    if not (gcfg["ANTI_NUKE"] and gcfg["RAID_MODE"]):
        return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        if entry.target.id == user.id:
            try:
                await entry.user.ban(reason="Tự động cấm kẻ tấn công RAID")
                await log_action(guild, "⚔️ Cấm kẻ tấn công", user.mention, moderator=entry.user, reason="Phát hiện cấm hàng loạt")
            except:
                pass
            break

# ========== LỆNH CHUNG ==========
@bot.command()
async def test(ctx):
    await ctx.send("🤖 Bot bảo vệ đang hoạt động!")

@bot.command(aliases=['help'])
async def dzai(ctx):
    embed1 = discord.Embed(title="🛡️ Danh sách lệnh (1/2)", color=discord.Color.green())
    embed1.add_field(name="!test", value="Kiểm tra bot", inline=False)
    embed1.add_field(name="!scan [số lượng]", value="Quét kênh xóa tin nhắn vi phạm", inline=False)
    embed1.add_field(name="!clear @user #kênh1 #kênh2 ...", value="Xóa toàn bộ tin nhắn của user trong các kênh", inline=False)
    embed1.add_field(name="!set_log_channel #kênh", value="Đặt kênh log", inline=False)
    embed1.add_field(name="!set_admin_role @role", value="Đặt role quản trị bot", inline=False)
    embed1.add_field(name="!add_admin_user @user", value="Thêm user được dùng lệnh", inline=False)
    embed1.add_field(name="!remove_admin_user @user", value="Xóa user khỏi danh sách admin", inline=False)
    embed1.add_field(name="!list_admin_users", value="Xem danh sách user admin", inline=False)
    embed1.add_field(name="!blacklist add @user", value="Thêm user vào danh sách đen", inline=False)
    embed1.add_field(name="!blacklist remove @user", value="Xóa user khỏi blacklist", inline=False)
    embed1.add_field(name="!mute @user <thời gian> [lý do]", value="Mute user", inline=False)
    embed1.add_field(name="!unmute @user", value="Gỡ mute", inline=False)
    embed1.add_field(name="!kick @user", value="Kick", inline=False)
    embed1.add_field(name="!ban @user", value="Ban", inline=False)
    embed1.add_field(name="!unban <user_id>", value="Unban", inline=False)
    embed1.add_field(name="!solo @user <sl> <nội dung> [delay] [emoji]", value="Đấu spam", inline=False)
    embed1.add_field(name="!stop_solo", value="Dừng solo", inline=False)

    embed2 = discord.Embed(title="🛡️ Danh sách lệnh (2/2)", color=discord.Color.blue())
    embed2.add_field(name="!allow_channel #kênh", value="Miễn anti spam", inline=False)
    embed2.add_field(name="!translate_channel #kênh", value="Chỉ dịch trong kênh", inline=False)
    embed2.add_field(name="!remove_translate_channel #kênh", value="Xóa auto dịch trong kênh", inline=False)
    embed2.add_field(name="!allow_role @role", value="Cho phép role gửi link invite", inline=False)
    embed2.add_field(name="!allow_badword <từ>", value="Cho phép từ được chửi bậy", inline=False)
    embed2.add_field(name="!allow_bad_member @user", value="Cho phép user đươc chửi bậy", inline=False)
    embed2.add_field(name="!add_scam_domain <domain>", value="Thêm domain lừa đảo", inline=False)
    embed2.add_field(name="!add_badword <từ>", value="Thêm từ cấm", inline=False)
    embed2.add_field(name="!add_scam_image_keyword <từ>", value="Thêm từ khóa scam ảnh", inline=False)
    embed2.add_field(name="!add_nsfw_keyword <từ>", value="Thêm từ khóa NSFW", inline=False)
    embed2.add_field(name="!toggle_nuke on/off", value="Bật/tắt anti-nuke", inline=False)
    embed2.add_field(name="!toggle_spam on/off", value="Bật/tắt anti-spam", inline=False)
    embed2.add_field(name="!toggle_raid on/off", value="Bật/tắt anti-raid", inline=False)
    embed2.add_field(name="!raid_mode_status", value="Trạng thái RAID", inline=False)
    embed2.add_field(name="!reset_raid_mode", value="Tắt RAID mode", inline=False)
    embed2.add_field(name="!reset_violations @user", value="Reset số lần vi phạm", inline=False)
    embed2.add_field(name="!check_violations @user", value="Xem số lần vi phạm", inline=False)

    await ctx.send(embed=embed1)
    await ctx.send(embed=embed2)

# ========== LỆNH QUÉT ==========
@bot.command()
async def scan(ctx, limit: int = 9999):
    if not await check_admin_permission(ctx):
        return
    if limit > 10000:
        await ctx.send("❌ Chỉ có thể quét tối đa 9999 tin nhắn.")
        return
    await ctx.send(f"🔍 Đang quét tối đa {limit} tin nhắn gần nhất...")
    deleted = 0
    async for message in ctx.channel.history(limit=limit):
        if message.author == bot.user:
            continue
        content = message.content.lower()
        is_violation = False
        if contains_bad_words(content, message.author.id, ctx.guild.id):
            is_violation = True
        if contains_scam(content):
            is_violation = True
        if INVITE_PATTERN.search(content):
            is_violation = True
        if contains_nsfw(content):
            is_violation = True
        if is_violation:
            try:
                await message.delete()
                deleted += 1
                await asyncio.sleep(0.5)
            except:
                pass
    await ctx.send(f"✅ Đã quét xong! Đã xóa {deleted} tin nhắn vi phạm.")

# ========== LỆNH CLEAR ==========
@bot.command()
async def clear(ctx, member: discord.Member, *channels: discord.TextChannel):
    """Xóa tất cả tin nhắn của member trong các kênh chỉ định"""
    if not await check_admin_permission(ctx):
        return
    if not channels:
        await ctx.send("❌ Vui lòng chỉ định ít nhất một kênh. Ví dụ: `!clear @user #kênh1 #kênh2`")
        return
    total_deleted = 0
    for channel in channels:
        await ctx.send(f"🔍 Đang quét kênh {channel.mention}...")
        async for message in channel.history(limit=None):
            if message.author.id == member.id:
                try:
                    await message.delete()
                    total_deleted += 1
                    await asyncio.sleep(0.5)
                except:
                    pass
    await ctx.send(f"✅ Đã xóa {total_deleted} tin nhắn của {member.mention}.")

# ========== LỆNH BLACKLIST ==========
@bot.command()
@commands.has_permissions(administrator=True)
async def blacklist(ctx, action: str, user: discord.User = None):
    if action.lower() not in ["add", "remove"]:
        await ctx.send("❌ Hành động chỉ có `add` hoặc `remove`.")
        return
    if not user:
        await ctx.send("❌ Vui lòng tag user. Ví dụ: `!blacklist add @user`")
        return
    gcfg = guild_config[ctx.guild.id]
    if action.lower() == "add":
        if user.id in gcfg["blacklist"]:
            await ctx.send(f"ℹ️ {user.mention} đã có trong blacklist.")
            return
        gcfg["blacklist"].append(user.id)
        await ctx.send(f"✅ Đã thêm {user.mention} vào blacklist.")
    else:
        if user.id not in gcfg["blacklist"]:
            await ctx.send(f"ℹ️ {user.mention} không có trong blacklist.")
            return
        gcfg["blacklist"].remove(user.id)
        await ctx.send(f"✅ Đã xóa {user.mention} khỏi blacklist.")

# ========== LỆNH DỊCH CHANNEL ==========
@bot.command()
@commands.has_permissions(administrator=True)
async def translate_channel(ctx, channel: discord.TextChannel):
    gcfg = guild_config[ctx.guild.id]
    if channel.id in gcfg["TRANSLATE_CHANNELS"]:
        await ctx.send(f"ℹ️ {channel.mention} đã được bật dịch.")
        return
    gcfg["TRANSLATE_CHANNELS"].append(channel.id)
    await ctx.send(f"✅ Đã bật auto dịch trong {channel.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def remove_translate_channel(ctx, channel: discord.TextChannel):
    gcfg = guild_config[ctx.guild.id]
    if channel.id not in gcfg["TRANSLATE_CHANNELS"]:
        await ctx.send(f"ℹ️ {channel.mention} chưa được bật dịch.")
        return
    gcfg["TRANSLATE_CHANNELS"].remove(channel.id)
    await ctx.send(f"✅ Đã tắt auto dịch trong {channel.mention}.")

# ========== LỆNH ALLOW CHANNEL (CHỈ MIỄN SPAM) ==========
@bot.command()
@commands.has_permissions(administrator=True)
async def allow_channel(ctx, channel: discord.TextChannel):
    gcfg = guild_config[ctx.guild.id]
    if channel.id in gcfg["ALLOWED_CHANNELS"]:
        await ctx.send(f"ℹ️ {channel.mention} đã được miễn anti spam.")
        return
    gcfg["ALLOWED_CHANNELS"].append(channel.id)
    await ctx.send(f"✅ Cho phép spam trong {channel.mention}.")

# ========== CÁC LỆNH CẤU HÌNH KHÁC (RIÊNG SERVER) ==========
@bot.command()
@commands.is_owner()
async def set_admin_role(ctx, role: discord.Role):
    gcfg = guild_config[ctx.guild.id]
    gcfg["ALLOWED_ROLE_ID"] = role.id
    await ctx.send(f"✅ Đã đặt role quản trị bot là {role.mention}.")

@bot.command()
@commands.is_owner()
async def add_admin_user(ctx, user: discord.User):
    gcfg = guild_config[ctx.guild.id]
    if user.id in gcfg["ALLOWED_USERS"]:
        await ctx.send(f"ℹ️ {user.mention} đã có trong danh sách.")
        return
    gcfg["ALLOWED_USERS"].append(user.id)
    await ctx.send(f"✅ Đã thêm {user.mention} vào danh sách admin.")

@bot.command()
@commands.is_owner()
async def remove_admin_user(ctx, user: discord.User):
    gcfg = guild_config[ctx.guild.id]
    if user.id not in gcfg["ALLOWED_USERS"]:
        await ctx.send(f"ℹ️ {user.mention} không có trong danh sách.")
        return
    gcfg["ALLOWED_USERS"].remove(user.id)
    await ctx.send(f"✅ Đã xóa {user.mention} khỏi danh sách admin.")

@bot.command()
@commands.is_owner()
async def list_admin_users(ctx):
    gcfg = guild_config[ctx.guild.id]
    if not gcfg["ALLOWED_USERS"]:
        await ctx.send("📃 Chưa có user nào được thêm.")
        return
    mentions = []
    for uid in gcfg["ALLOWED_USERS"]:
        try:
            u = await bot.fetch_user(uid)
            mentions.append(u.mention)
        except:
            mentions.append(f"<@{uid}>")
    await ctx.send(f"📋 **Admin users:** {', '.join(mentions)}")

@bot.command()
async def set_log_channel(ctx, channel: discord.TextChannel = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if channel is None:
        channel = ctx.channel
    gcfg["log_channel_id"] = channel.id
    await ctx.send(f"✅ Kênh log: {channel.mention}")

@bot.command()
async def toggle_nuke(ctx, status: str = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if status is None:
        gcfg["ANTI_NUKE"] = not gcfg["ANTI_NUKE"]
    else:
        gcfg["ANTI_NUKE"] = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Nuke: {'BẬT' if gcfg['ANTI_NUKE'] else 'TẮT'}")

@bot.command()
async def toggle_spam(ctx, status: str = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if status is None:
        gcfg["ANTI_SPAM"] = not gcfg["ANTI_SPAM"]
    else:
        gcfg["ANTI_SPAM"] = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Spam: {'BẬT' if gcfg['ANTI_SPAM'] else 'TẮT'}")

@bot.command()
async def toggle_raid(ctx, status: str = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if status is None:
        gcfg["ANTI_RAID"] = not gcfg["ANTI_RAID"]
    else:
        gcfg["ANTI_RAID"] = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Raid: {'BẬT' if gcfg['ANTI_RAID'] else 'TẮT'}")

@bot.command()
async def allow_role(ctx, role: discord.Role):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if role.id in gcfg["ALLOWED_ROLES"]:
        await ctx.send(f"ℹ️ Role {role.name} đã được cho phép.")
        return
    gcfg["ALLOWED_ROLES"].append(role.id)
    await ctx.send(f"✅ Cho phép gửi link invite với role {role.name}")

@bot.command()
async def add_scam_domain(ctx, domain: str):
    if not await check_admin_permission(ctx): return
    SCAM_DOMAINS.append(domain.lower())
    await ctx.send(f"✅ Đã thêm domain {domain}")

@bot.command()
async def add_badword(ctx, *, word: str):
    if not await check_admin_permission(ctx): return
    BAD_WORDS.append(word.lower())
    await ctx.send(f"✅ Đã thêm từ cấm {word}")

@bot.command()
async def add_scam_image_keyword(ctx, *, keyword: str):
    if not await check_admin_permission(ctx): return
    SCAM_IMAGE_KEYWORDS.append(keyword.lower())
    await ctx.send(f"✅ Thêm từ khóa ảnh scam: {keyword}")

@bot.command()
async def add_nsfw_keyword(ctx, *, keyword: str):
    if not await check_admin_permission(ctx): return
    NSFW_KEYWORDS.append(keyword.lower())
    await ctx.send(f"✅ Thêm từ khóa NSFW: {keyword}")

@bot.command()
async def allow_badword(ctx, *, word: str):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    word_lower = word.lower()
    if word_lower in gcfg["ALLOWED_BAD_WORDS"]:
        await ctx.send(f"ℹ️ Từ '{word}' đã được cho phép.")
        return
    gcfg["ALLOWED_BAD_WORDS"].append(word_lower)
    await ctx.send(f"✅ Đã cho phép từ '{word}'.")

@bot.command()
async def remove_allow_badword(ctx, *, word: str):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    word_lower = word.lower()
    if word_lower not in gcfg["ALLOWED_BAD_WORDS"]:
        await ctx.send(f"ℹ️ Từ '{word}' không có trong danh sách.")
        return
    gcfg["ALLOWED_BAD_WORDS"].remove(word_lower)
    await ctx.send(f"✅ Đã xóa từ '{word}'.")

@bot.command()
async def allow_bad_member(ctx, member: discord.Member):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if member.id in gcfg["ALLOWED_BAD_MEMBERS"]:
        await ctx.send(f"ℹ️ {member.mention} đã được cho phép.")
        return
    gcfg["ALLOWED_BAD_MEMBERS"].append(member.id)
    await ctx.send(f"✅ Đã cho phép {member.mention} chửi bậy.")

@bot.command()
async def remove_allow_bad_member(ctx, member: discord.Member):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if member.id not in gcfg["ALLOWED_BAD_MEMBERS"]:
        await ctx.send(f"ℹ️ {member.mention} không có trong danh sách.")
        return
    gcfg["ALLOWED_BAD_MEMBERS"].remove(member.id)
    await ctx.send(f"✅ Đã xóa {member.mention} khỏi danh sách.")

@bot.command()
async def raid_mode_status(ctx):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    await ctx.send(f"🚨 RAID MODE: {'BẬT' if gcfg['RAID_MODE'] else 'TẮT'}")

@bot.command()
async def reset_raid_mode(ctx):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    gcfg["RAID_MODE"] = False
    gcfg["join_times"].clear()
    await ctx.send("✅ Đã tắt RAID mode và reset bộ đếm.")

@bot.command()
async def reset_violations(ctx, member: discord.Member):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if member.id in gcfg["violation_count"]:
        del gcfg["violation_count"][member.id]
        await ctx.send(f"✅ Reset vi phạm cho {member.mention}")
    else:
        await ctx.send(f"ℹ️ {member.mention} chưa vi phạm lần nào.")

@bot.command()
async def check_violations(ctx, member: discord.Member = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if member is None:
        member = ctx.author
    count = gcfg["violation_count"].get(member.id, 0)
    await ctx.send(f"📊 {member.mention} có {count}/5 lần vi phạm.")

# ========== LỆNH MUTE, KICK, BAN, UNMUTE, UNBAN ==========
@bot.command()
async def mute(ctx, member: discord.Member, duration: str = "1h", *, reason: str = "Không có lý do"):
    if not await check_admin_permission(ctx): return
    units = {"m": 60, "h": 3600, "d": 86400}
    try:
        if duration[-1] in units:
            seconds = int(duration[:-1]) * units[duration[-1]]
        else:
            seconds = int(duration) * 60
    except:
        seconds = 3600
    until = discord.utils.utcnow() + timedelta(seconds=seconds)
    await member.timeout(until, reason=reason)
    await ctx.send(f"🔇 Đã mute {member.mention} trong **{duration}** (lý do: {reason})")

@bot.command()
async def unmute(ctx, member: discord.Member, *, reason: str = "Không có lý do"):
    if not await check_admin_permission(ctx): return
    await member.timeout(None, reason=reason)
    await ctx.send(f"🔊 Đã unmute {member.mention} (lý do: {reason})")

@bot.command()
async def kick(ctx, member: discord.Member, *, reason: str = "Không có lý do"):
    if not await check_admin_permission(ctx): return
    await member.kick(reason=reason)
    await ctx.send(f"👢 Đã kick {member.mention} (lý do: {reason})")

@bot.command()
async def ban(ctx, member: discord.Member, *, reason: str = "Không có lý do"):
    if not await check_admin_permission(ctx): return
    await member.ban(reason=reason)
    await ctx.send(f"🔨 Đã ban {member.mention} (lý do: {reason})")

@bot.command()
async def unban(ctx, user_id: int, *, reason: str = "Không có lý do"):
    if not await check_admin_permission(ctx): return
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"✅ Đã unban {user.mention} (ID: {user_id})")
    except discord.NotFound:
        await ctx.send(f"❌ Không tìm thấy user ID {user_id} hoặc chưa bị ban.")
    except discord.Forbidden:
        await ctx.send("❌ Bot không có quyền unban.")
    except Exception as e:
        await ctx.send(f"❌ Lỗi: {e}")

# ========== LỆNH SOLO (CÓ DELAY) ==========
@bot.command()
async def solo(ctx, target: discord.Member, amount: int, delay: float = 0.5, *, content_with_emoji: str = ""):
    if not await check_admin_permission(ctx):
        return
    if amount > 9999:
        await ctx.send("❌ Số lượng không được vượt quá 9999.")
        return
    if amount <= 0:
        await ctx.send("❌ Số lượng phải lớn hơn 0.")
        return
    if delay < 0:
        delay = 0

    # Tách emoji nếu có (từ cuối)
    words = content_with_emoji.rsplit(' ', 1)
    if len(words) == 2 and re.search(r'[\U00010000-\U0010FFFF]', words[1]):
        content, emoji = words
    else:
        content, emoji = content_with_emoji, ""
    if not content:
        content = "spam"
    msg = f"{content} {emoji}".strip()

    if ctx.channel.id in solo_tasks and not solo_tasks[ctx.channel.id].done():
        solo_tasks[ctx.channel.id].cancel()
        await ctx.send("⏹️ Đã hủy solo cũ trong kênh này.")
        await asyncio.sleep(0.5)

    await ctx.send(f"🎮 Bắt đầu solo {amount} lần với {target.mention}! (delay {delay}s, nội dung: {msg})")
    async def spam_task():
        try:
            for i in range(amount):
                await ctx.send(f"{target.mention} {msg}")
                await asyncio.sleep(delay)
            await ctx.send(f"✅ Solo xong {amount} lần với {target.mention}!")
        except asyncio.CancelledError:
            await ctx.send(f"⏹️ Solo với {target.mention} đã bị dừng lại sau {i+1 if 'i' in locals() else 0} lần.")
            raise
        finally:
            if ctx.channel.id in solo_tasks:
                del solo_tasks[ctx.channel.id]
    task = asyncio.create_task(spam_task())
    solo_tasks[ctx.channel.id] = task

@bot.command()
async def stop_solo(ctx):
    if ctx.channel.id in solo_tasks and not solo_tasks[ctx.channel.id].done():
        solo_tasks[ctx.channel.id].cancel()
        await ctx.send("⏹️ Đã yêu cầu dừng solo.")
    else:
        await ctx.send("❌ Không có solo nào trong kênh này.")

# ========== CHẠY BOT ==========
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Thiếu TOKEN")
    else:
        keep_alive()
        bot.run(TOKEN)
