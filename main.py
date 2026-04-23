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

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ========== CẤU HÌNH ==========
OWNER_ID = 1075422580833529879   # 👈 THAY BẰNG ID DISCORD CỦA BẠN

SPAM_THRESHOLD = 5
SPAM_WINDOW = 1
EMOJI_SPAM_LIMIT = 5
STICKER_SPAM_LIMIT = 5
STICKER_WINDOW = 1
RAID_THRESHOLD = 5
RAID_WINDOW = 1
MAX_LINE_LENGTH = 20

ANTI_NUKE = True
ANTI_SPAM = True
ANTI_RAID = True
AUTO_TRANSLATE = True   # Bật/tắt auto dịch

ALLOWED_ROLE_ID = None
ALLOWED_USERS = []

# Danh sách trắng cho phép chửi bậy (từ và thành viên)
ALLOWED_BAD_WORDS = []     # Các từ được phép chửi (whitelist)
ALLOWED_BAD_MEMBERS = []   # ID thành viên được phép chửi

# Quản lý task solo
solo_tasks = {}

BAD_WORDS = ["địt","lồn","cặc","đụ","vcl","dm","đmm","cc","cac","duma","ditme","fuck","shit","bitch","đĩ","mẹ kiếp","chết tiệt","mẹ mày","nigga","nigger","nick her"]
SCAM_DOMAINS = ["bit.ly","tinyurl.com","rebrand.ly","discord.gift","steamcommmunity.com","nitro-steam.com","free-discord-nitro.com"]
SCAM_IMAGE_KEYWORDS = ["mrbeast","mr beast","jj","j.j","giveaway","quà tặng","free nitro","free steam","free gift","trúng thưởng","nhận quà","100% free"]
NSFW_KEYWORDS = ["nsfw","18+","porn","sex","adult","xxx","hentai","dirty","khiêu dâm","người lớn","18plus","sexviet","vlxx","phim sex"]
INVITE_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?discord(?:app)?\.(?:com|gg)/(?:invite/)?([a-zA-Z0-9\-]+)", re.IGNORECASE)

ALLOWED_CHANNELS = []      # Kênh được phép spam/link (whitelist)
ALLOWED_ROLES = []
user_messages = defaultdict(list)
user_stickers = defaultdict(list)
join_times = []
RAID_MODE = False
log_channel_id = None
violation_count = defaultdict(int)

# ========== HÀM KIỂM TRA QUYỀN ==========
async def check_admin_permission(ctx):
    if ctx.author.id == OWNER_ID:
        return True
    if ctx.author.id == ctx.guild.owner_id:
        return True
    if ctx.author.id in ALLOWED_USERS:
        return True
    if ALLOWED_ROLE_ID is not None:
        role = ctx.guild.get_role(ALLOWED_ROLE_ID)
        if role and role in ctx.author.roles:
            return True
    await ctx.send("❌ Bạn không có quyền dùng lệnh này!")
    return False

# ========== HÀM HỖ TRỢ ==========
async def log_action(guild, action, target, moderator=None, reason=None):
    if not log_channel_id:
        return
    channel = bot.get_channel(log_channel_id)
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

def count_emojis(content):
    unicode_pattern = re.compile(r'[\U00010000-\U0010FFFF]', flags=re.UNICODE)
    unicode_count = len(unicode_pattern.findall(content))
    custom_pattern = re.compile(r'<a?:\w+:\d+>')
    custom_count = len(custom_pattern.findall(content))
    return unicode_count + custom_count

def contains_bad_words(content, author_id):
    # Nếu author được phép chửi thì bỏ qua
    if author_id in ALLOWED_BAD_MEMBERS:
        return False
    c = content.lower()
    # Kiểm tra từng từ trong BAD_WORDS, nếu từ đó nằm trong ALLOWED_BAD_WORDS thì bỏ qua
    for word in BAD_WORDS:
        if word in c:
            if word in ALLOWED_BAD_WORDS:
                continue
            return True
    return False

def count_lines(content):
    return content.count('\n') + 1 if content else 0

async def is_allowed_link(ctx, content):
    if ctx.channel.id in ALLOWED_CHANNELS:
        return True
    return any(role.id in ALLOWED_ROLES for role in ctx.author.roles)

def contains_scam_image_keywords(content):
    c = content.lower()
    return any(kw in c for kw in SCAM_IMAGE_KEYWORDS)

def contains_nsfw_link(content):
    c = content.lower()
    urls = re.findall(r'https?://\S+', c)
    for url in urls:
        if any(kw in url for kw in NSFW_KEYWORDS):
            return True
    return False

async def handle_violation(user, guild, reason):
    violation_count[user.id] += 1
    current = violation_count[user.id]
    await log_action(guild, f"⚠️ Vi phạm lần {current}/5", user.mention, moderator=bot.user, reason=reason)
    if current >= 5:
        try:
            await guild.ban(user, reason=f"Tự động ban sau 5 lần vi phạm: {reason}")
            await log_action(guild, "🔨 ĐÃ BAN", user.mention, moderator=bot.user, reason=f"5 lần vi phạm (lần cuối: {reason})")
            del violation_count[user.id]
            return True
        except:
            pass
    return False

# ========== AUTO DỊCH ==========
async def translate_text(text, src='auto', dest='vi'):
    try:
        translated = GoogleTranslator(source=src, target=dest).translate(text)
        return translated
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

    # Auto dịch (chỉ dịch tin nhắn text, không phải lệnh)
    if AUTO_TRANSLATE and message.content and not message.content.startswith('!'):
        # Phát hiện ngôn ngữ và dịch sang tiếng Việt nếu không phải tiếng Việt
        try:
            translator = GoogleTranslator()
            detected_lang = translator.detect(message.content)
            if detected_lang and detected_lang != 'vi':
                translated = await translate_text(message.content)
                if translated and translated.lower() != message.content.lower():
                    await message.reply(f"🌐 **Dịch sang tiếng Việt:** {translated}")
        except:
            pass

    # Miễn trừ cho admin/owner (bao gồm cả kiểm tra chửi bậy)
    if message.author.id == OWNER_ID or message.author.guild_permissions.administrator or message.author.id == message.guild.owner_id:
        await bot.process_commands(message)
        return

    ctx = await bot.get_context(message)
    content = message.content
    user = message.author
    guild = message.guild

    async def punish(violation_reason):
        await message.delete()
        banned = await handle_violation(user, guild, violation_reason)
        if not banned:
            try:
                await user.timeout(timedelta(hours=1), reason=violation_reason)
                await log_action(guild, "🔇 Auto Mute", user.mention, moderator=bot.user, reason=f"{violation_reason} (1 giờ)")
            except:
                pass

    # Nếu kênh được allow thì bỏ qua mọi kiểm tra spam/raid/scam/chửi bậy
    if ctx.channel.id in ALLOWED_CHANNELS:
        await bot.process_commands(message)
        return

    if ANTI_SPAM:
        emoji_count = count_emojis(content)
        if emoji_count > EMOJI_SPAM_LIMIT:
            await punish(f"Spam emoji ({emoji_count})")
            return

        if message.stickers:
            now = time.time()
            user_stickers[user.id].append(now)
            user_stickers[user.id] = [t for t in user_stickers[user.id] if now - t < STICKER_WINDOW]
            if len(user_stickers[user.id]) > STICKER_SPAM_LIMIT:
                await punish(f"Spam sticker ({len(user_stickers[user.id])})")
                return

        if contains_bad_words(content, user.id):
            await punish(f"Chửi bậy: {content[:50]}")
            return

        if count_lines(content) > MAX_LINE_LENGTH:
            await punish(f"Tin nhắn dài {count_lines(content)} dòng")
            return

        now = time.time()
        user_messages[user.id].append(now)
        user_messages[user.id] = [t for t in user_messages[user.id] if now - t < SPAM_WINDOW]
        if len(user_messages[user.id]) > SPAM_THRESHOLD:
            await punish(f"Spam tin nhắn ({len(user_messages[user.id])} tin / {SPAM_WINDOW}s)")
            return

    # Các kiểm tra link scam, invite, ảnh scam, NSFW (vẫn áp dụng cho mọi kênh trừ ALLOWED_CHANNELS)
    urls = re.findall(r"(?:https?://)?(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})?)", content)
    for url in urls:
        if any(scam in url for scam in SCAM_DOMAINS):
            if not await is_allowed_link(ctx, url):
                await punish(f"Link lừa đảo: {url}")
                return

    if INVITE_PATTERN.search(content) and not await is_allowed_link(ctx, content):
        await punish("Gửi link mời Discord không được phép")
        return

    if message.attachments:
        for att in message.attachments:
            if att.filename.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp')):
                if contains_scam_image_keywords(content) or contains_scam_image_keywords(att.filename):
                    await punish(f"Ảnh scam: {att.filename}")
                    return

    if contains_nsfw_link(content) and not await is_allowed_link(ctx, content):
        await punish("Gửi link NSFW (server người lớn)")
        return

    await bot.process_commands(message)

# ===== CÁC SỰ KIỆN KHÁC GIỮ NGUYÊN =====
@bot.event
async def on_member_join(member):
    if not (ANTI_RAID and ANTI_NUKE):
        return
    global RAID_MODE
    now = time.time()
    join_times.append(now)
    join_times[:] = [t for t in join_times if now - t < RAID_WINDOW]
    if len(join_times) >= RAID_THRESHOLD:
        RAID_MODE = True
        await log_action(member.guild, "🚨 KÍCH HOẠT CHẾ ĐỘ RAID", member.mention, moderator=bot.user, reason=f"{len(join_times)} join trong {RAID_WINDOW}s")

@bot.event
async def on_guild_channel_create(channel):
    if not (ANTI_NUKE and RAID_MODE):
        return
    await channel.delete()
    await log_action(channel.guild, "🧨 Chặn tạo kênh", channel.name, moderator=bot.user, reason="RAID MODE")

@bot.event
async def on_guild_role_create(role):
    if not (ANTI_NUKE and RAID_MODE):
        return
    await role.delete()
    await log_action(role.guild, "🧨 Chặn tạo role", role.name, moderator=bot.user, reason="RAID MODE")

@bot.event
async def on_member_ban(guild, user):
    if not (ANTI_NUKE and RAID_MODE):
        return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        if entry.target.id == user.id:
            try:
                await entry.user.ban(reason="Tự động cấm kẻ tấn công RAID")
                await log_action(guild, "⚔️ Cấm kẻ tấn công", user.mention, moderator=entry.user, reason="Phát hiện cấm hàng loạt")
            except:
                pass
            break

# ========== LỆNH ==========
@bot.command()
async def test(ctx):
    await ctx.send("🤖 Bot bảo vệ đang hoạt động!")

@bot.command(aliases=['help'])
async def dzai(ctx):
    """Hiển thị danh sách lệnh dạng embed (chia 2 embed để tránh giới hạn 25 fields)"""
    # Embed 1: Các lệnh quản lý user & lệnh tương tác
    embed1 = discord.Embed(title="🛡️ Danh sách lệnh (Phần 1/2)", color=discord.Color.green())
    embed1.add_field(name="!test", value="Kiểm tra bot", inline=False)
    embed1.add_field(name="!scan [số lượng]", value="Quét kênh xóa tin vi phạm (mặc định 100)", inline=False)
    embed1.add_field(name="!set_log_channel #kênh", value="Đặt kênh log", inline=False)
    embed1.add_field(name="!set_admin_role @role", value="Đặt role quản trị bot", inline=False)
    embed1.add_field(name="!add_admin_user @user", value="Thêm user được dùng lệnh", inline=False)
    embed1.add_field(name="!remove_admin_user @user", value="Xóa user khỏi danh sách admin", inline=False)
    embed1.add_field(name="!list_admin_users", value="Xem danh sách user admin", inline=False)
    embed1.add_field(name="!mute @user <thời gian> [lý do]", value="Mute (vd: 10m, 2h, 1d)", inline=False)
    embed1.add_field(name="!unmute @user [lý do]", value="Gỡ mute", inline=False)
    embed1.add_field(name="!kick @user [lý do]", value="Kick thành viên", inline=False)
    embed1.add_field(name="!ban @user [lý do]", value="Ban thành viên", inline=False)
    embed1.add_field(name="!unban <user_id> [lý do]", value="Unban bằng ID", inline=False)
    embed1.add_field(name="!solo @user <số_lượng> <nội dung> [emoji]", value="Đấu spam (dừng bằng !stop_solo)", inline=False)
    embed1.add_field(name="!stop_solo", value="Dừng solo trong kênh hiện tại", inline=False)
    embed1.add_field(name="!toggle_translate on/off", value="Bật/tắt auto dịch", inline=False)

    # Embed 2: Các lệnh cấu hình bảo vệ, allow, thêm từ cấm
    embed2 = discord.Embed(title="🛡️ Danh sách lệnh (Phần 2/2)", color=discord.Color.blue())
    embed2.add_field(name="!allow_channel #kênh", value="Cho phép kênh được spam/link (whitelist)", inline=False)
    embed2.add_field(name="!allow_role @role", value="Cho phép role gửi link", inline=False)
    embed2.add_field(name="!allow_badword <từ>", value="Cho phép 1 từ chửi bậy (whitelist)", inline=False)
    embed2.add_field(name="!allow_bad_member @user", value="Cho phép thành viên chửi bậy", inline=False)
    embed2.add_field(name="!remove_allow_badword <từ>", value="Xóa từ khỏi whitelist chửi bậy", inline=False)
    embed2.add_field(name="!remove_allow_bad_member @user", value="Xóa member khỏi whitelist", inline=False)
    embed2.add_field(name="!add_scam_domain <domain>", value="Thêm domain lừa đảo", inline=False)
    embed2.add_field(name="!add_badword <từ>", value="Thêm từ chửi bậy (cấm)", inline=False)
    embed2.add_field(name="!add_scam_image_keyword <từ>", value="Thêm từ khóa ảnh scam", inline=False)
    embed2.add_field(name="!add_nsfw_keyword <từ>", value="Thêm từ khóa NSFW", inline=False)
    embed2.add_field(name="!toggle_nuke on/off", value="Bật/tắt chống Nuke", inline=False)
    embed2.add_field(name="!toggle_spam on/off", value="Bật/tắt chống spam", inline=False)
    embed2.add_field(name="!toggle_raid on/off", value="Bật/tắt chống raid", inline=False)
    embed2.add_field(name="!raid_mode_status", value="Xem trạng thái RAID", inline=False)
    embed2.add_field(name="!reset_raid_mode", value="Tắt RAID mode", inline=False)
    embed2.add_field(name="!reset_violations @user", value="Reset số lần vi phạm", inline=False)
    embed2.add_field(name="!check_violations @user", value="Xem số lần vi phạm", inline=False)

    await ctx.send(embed=embed1)
    await ctx.send(embed=embed2)

# ========== LỆNH QUÉT (SCAN) ==========
@bot.command()
async def scan(ctx, limit: int = 100):
    """Quét kênh hiện tại, xóa tin nhắn vi phạm (chửi bậy, scam, nsfw, link mời)"""
    if not await check_admin_permission(ctx):
        return
    if limit > 1000:
        await ctx.send("❌ Chỉ có thể quét tối đa 1000 tin nhắn.")
        return
    await ctx.send(f"🔍 Đang quét {limit} tin nhắn gần nhất trong kênh...")
    deleted = 0
    async for message in ctx.channel.history(limit=limit):
        if message.author == bot.user:
            continue
        content = message.content.lower()
        # Kiểm tra các tiêu chí vi phạm
        is_violation = False
        # 1. Chửi bậy
        if contains_bad_words(content, message.author.id):
            is_violation = True
        # 2. Link scam
        for url in re.findall(r"(?:https?://)?(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})?)", content):
            if any(scam in url for scam in SCAM_DOMAINS):
                is_violation = True
                break
        # 3. Link mời Discord
        if INVITE_PATTERN.search(content):
            is_violation = True
        # 4. NSFW link
        if contains_nsfw_link(content):
            is_violation = True
        # 5. Ảnh scam (chỉ kiểm tra caption, không thể kiểm tra ảnh cũ dễ dàng)
        for att in message.attachments:
            if att.filename.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp')):
                if contains_scam_image_keywords(content) or contains_scam_image_keywords(att.filename):
                    is_violation = True
                    break
        if is_violation:
            try:
                await message.delete()
                deleted += 1
                await asyncio.sleep(0.5)  # tránh rate limit
            except:
                pass
    await ctx.send(f"✅ Đã quét xong! Đã xóa {deleted} tin nhắn vi phạm.")

# ========== LỆNH OWNER ==========
@bot.command()
@commands.is_owner()
async def set_admin_role(ctx, role: discord.Role):
    global ALLOWED_ROLE_ID
    ALLOWED_ROLE_ID = role.id
    await ctx.send(f"✅ Đã đặt role quản trị bot là {role.mention}.")

@bot.command()
@commands.is_owner()
async def add_admin_user(ctx, user: discord.User):
    if user.id in ALLOWED_USERS:
        await ctx.send(f"ℹ️ {user.mention} đã có trong danh sách.")
        return
    ALLOWED_USERS.append(user.id)
    await ctx.send(f"✅ Đã thêm {user.mention} vào danh sách admin.")

@bot.command()
@commands.is_owner()
async def remove_admin_user(ctx, user: discord.User):
    if user.id not in ALLOWED_USERS:
        await ctx.send(f"ℹ️ {user.mention} không có trong danh sách.")
        return
    ALLOWED_USERS.remove(user.id)
    await ctx.send(f"✅ Đã xóa {user.mention} khỏi danh sách admin.")

@bot.command()
@commands.is_owner()
async def list_admin_users(ctx):
    if not ALLOWED_USERS:
        await ctx.send("📃 Chưa có user nào được thêm.")
        return
    mentions = []
    for uid in ALLOWED_USERS:
        try:
            u = await bot.fetch_user(uid)
            mentions.append(u.mention)
        except:
            mentions.append(f"<@{uid}>")
    await ctx.send(f"📋 **Admin users:** {', '.join(mentions)}")

@bot.command()
@commands.is_owner()
async def toggle_translate(ctx, status: str = None):
    global AUTO_TRANSLATE
    if status is None:
        AUTO_TRANSLATE = not AUTO_TRANSLATE
    else:
        AUTO_TRANSLATE = status.lower() == "on"
    await ctx.send(f"🌐 Auto dịch: {'BẬT' if AUTO_TRANSLATE else 'TẮT'}")

# ========== LỆNH ALLOW (WHITELIST) ==========
@bot.command()
@commands.has_permissions(administrator=True)
async def allow_channel(ctx, channel: discord.TextChannel):
    ALLOWED_CHANNELS.append(channel.id)
    await ctx.send(f"✅ Cho phép spam/link trong {channel.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def allow_badword(ctx, *, word: str):
    """Cho phép một từ chửi bậy (không bị phạt)"""
    word_lower = word.lower()
    if word_lower in ALLOWED_BAD_WORDS:
        await ctx.send(f"ℹ️ Từ '{word}' đã có trong danh sách cho phép.")
        return
    ALLOWED_BAD_WORDS.append(word_lower)
    await ctx.send(f"✅ Đã cho phép từ '{word}'. Người dùng có thể chửi từ này mà không bị phạt.")

@bot.command()
@commands.has_permissions(administrator=True)
async def remove_allow_badword(ctx, *, word: str):
    """Xóa từ khỏi danh sách cho phép chửi bậy"""
    word_lower = word.lower()
    if word_lower not in ALLOWED_BAD_WORDS:
        await ctx.send(f"ℹ️ Từ '{word}' không có trong danh sách cho phép.")
        return
    ALLOWED_BAD_WORDS.remove(word_lower)
    await ctx.send(f"✅ Đã xóa từ '{word}' khỏi danh sách cho phép.")

@bot.command()
@commands.has_permissions(administrator=True)
async def allow_bad_member(ctx, member: discord.Member):
    """Cho phép thành viên được chửi bậy mà không bị phạt"""
    if member.id in ALLOWED_BAD_MEMBERS:
        await ctx.send(f"ℹ️ {member.mention} đã có trong danh sách cho phép.")
        return
    ALLOWED_BAD_MEMBERS.append(member.id)
    await ctx.send(f"✅ Đã cho phép {member.mention} chửi bậy mà không bị phạt.")

@bot.command()
@commands.has_permissions(administrator=True)
async def remove_allow_bad_member(ctx, member: discord.Member):
    """Xóa thành viên khỏi danh sách cho phép chửi bậy"""
    if member.id not in ALLOWED_BAD_MEMBERS:
        await ctx.send(f"ℹ️ {member.mention} không có trong danh sách cho phép.")
        return
    ALLOWED_BAD_MEMBERS.remove(member.id)
    await ctx.send(f"✅ Đã xóa {member.mention} khỏi danh sách cho phép chửi bậy.")

# ========== CÁC LỆNH QUẢN TRỊ KHÁC (GIỮ NGUYÊN) ==========
@bot.command()
async def set_log_channel(ctx, channel: discord.TextChannel = None):
    if not await check_admin_permission(ctx): return
    global log_channel_id
    if channel is None:
        channel = ctx.channel
    log_channel_id = channel.id
    await ctx.send(f"✅ Kênh log: {channel.mention}")

@bot.command()
async def toggle_nuke(ctx, status: str = None):
    if not await check_admin_permission(ctx): return
    global ANTI_NUKE
    if status is None:
        ANTI_NUKE = not ANTI_NUKE
    else:
        ANTI_NUKE = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Nuke: {'BẬT' if ANTI_NUKE else 'TẮT'}")

@bot.command()
async def toggle_spam(ctx, status: str = None):
    if not await check_admin_permission(ctx): return
    global ANTI_SPAM
    if status is None:
        ANTI_SPAM = not ANTI_SPAM
    else:
        ANTI_SPAM = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Spam: {'BẬT' if ANTI_SPAM else 'TẮT'}")

@bot.command()
async def toggle_raid(ctx, status: str = None):
    if not await check_admin_permission(ctx): return
    global ANTI_RAID
    if status is None:
        ANTI_RAID = not ANTI_RAID
    else:
        ANTI_RAID = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Raid: {'BẬT' if ANTI_RAID else 'TẮT'}")

@bot.command()
async def allow_role(ctx, role: discord.Role):
    if not await check_admin_permission(ctx): return
    ALLOWED_ROLES.append(role.id)
    await ctx.send(f"✅ Cho phép link với role {role.name}")

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
async def raid_mode_status(ctx):
    if not await check_admin_permission(ctx): return
    await ctx.send(f"🚨 RAID MODE: {'BẬT' if RAID_MODE else 'TẮT'}")

@bot.command()
async def reset_raid_mode(ctx):
    if not await check_admin_permission(ctx): return
    global RAID_MODE, join_times
    RAID_MODE = False
    join_times.clear()
    await ctx.send("✅ Đã tắt RAID mode và reset bộ đếm.")

@bot.command()
async def reset_violations(ctx, member: discord.Member):
    if not await check_admin_permission(ctx): return
    if member.id in violation_count:
        del violation_count[member.id]
        await ctx.send(f"✅ Reset vi phạm cho {member.mention}")
    else:
        await ctx.send(f"ℹ️ {member.mention} chưa vi phạm lần nào.")

@bot.command()
async def check_violations(ctx, member: discord.Member = None):
    if not await check_admin_permission(ctx): return
    if member is None:
        member = ctx.author
    count = violation_count.get(member.id, 0)
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
    if seconds > 28 * 86400:
        seconds = 28 * 86400
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
        await ctx.send(f"✅ Đã unban {user.mention} (ID: {user_id}) với lý do: {reason}")
    except discord.NotFound:
        await ctx.send(f"❌ Không tìm thấy user có ID {user_id} hoặc user chưa bị ban.")
    except discord.Forbidden:
        await ctx.send("❌ Bot không có quyền unban.")
    except Exception as e:
        await ctx.send(f"❌ Lỗi: {e}")

# ========== LỆNH SOLO ==========
@bot.command()
async def solo(ctx, target: discord.Member, amount: int, *, content_with_emoji: str = ""):
    if not await check_admin_permission(ctx):
        return
    if amount > 9999:
        await ctx.send("❌ Số lượng không được vượt quá 9999.")
        return
    if amount <= 0:
        await ctx.send("❌ Số lượng phải lớn hơn 0.")
        return
    
    content = content_with_emoji
    emoji = ""
    words = content_with_emoji.rsplit(' ', 1)
    if len(words) == 2:
        last_word = words[1]
        emoji_pattern = re.compile(r'[\U00010000-\U0010FFFF]', flags=re.UNICODE)
        if emoji_pattern.search(last_word):
            emoji = last_word
            content = words[0]
        else:
            content = content_with_emoji
    else:
        content = content_with_emoji
    
    if not content:
        content = "spam"
    
    msg_content = content
    if emoji:
        msg_content += f" {emoji}"
    
    if ctx.channel.id in solo_tasks and not solo_tasks[ctx.channel.id].done():
        solo_tasks[ctx.channel.id].cancel()
        await ctx.send("⏹️ Đã hủy solo cũ trong kênh này.")
        await asyncio.sleep(0.5)
    
    await ctx.send(f"🎮 Bắt đầu solo {amount} lần với {target.mention}! (nội dung: {msg_content})")
    await asyncio.sleep(0.5)
    
    async def spam_task():
        try:
            for i in range(amount):
                await ctx.send(f"{target.mention} {msg_content}")
                if amount > 200:
                    await asyncio.sleep(0.5)
                elif amount > 50:
                    await asyncio.sleep(0.3)
                elif amount > 10:
                    await asyncio.sleep(0.1)
            await ctx.send(f"✅ Đã solo xong {amount} lần với {target.mention}!")
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
        await ctx.send("⏹️ Đã yêu cầu dừng solo. Vui lòng chờ một lát...")
    else:
        await ctx.send("❌ Hiện không có solo nào đang chạy trong kênh này.")

# ========== CHẠY BOT ==========
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Không tìm thấy DISCORD_TOKEN trong biến môi trường.")
    else:
        keep_alive()
        bot.run(TOKEN)
