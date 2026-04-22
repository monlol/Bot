import discord
from discord.ext import commands
from datetime import timedelta
from collections import defaultdict
import re
import time
from dotenv import load_dotenv
import os
from keep_alive import keep_alive

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, dzai_command=None)

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

ALLOWED_ROLE_ID = None

BAD_WORDS = ["địt","lồn","cặc","đụ","vcl","dm","đmm","cc","loz","lol","cac","duma","ditme","fuck","shit","bitch","đĩ","mẹ kiếp","chết tiệt"]
SCAM_DOMAINS = ["bit.ly","tinyurl.com","rebrand.ly","discord.gift","steamcommmunity.com","nitro-steam.com","free-discord-nitro.com"]
SCAM_IMAGE_KEYWORDS = ["mrbeast","mr beast","jj","j.j","giveaway","quà tặng","free nitro","free steam","free gift","trúng thưởng","nhận quà","100% free"]
NSFW_KEYWORDS = ["nsfw","18+","porn","sex","adult","xxx","hentai","dirty","khiêu dâm","người lớn","18plus","sexviet","vlxx","phim sex"]
INVITE_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?discord(?:app)?\.(?:com|gg)/(?:invite/)?([a-zA-Z0-9\-]+)", re.IGNORECASE)

ALLOWED_CHANNELS = []
ALLOWED_ROLES = []
user_messages = defaultdict(list)
user_stickers = defaultdict(list)
join_times = []
RAID_MODE = True
log_channel_id = None
violation_count = defaultdict(int)

# ========== HÀM KIỂM TRA QUYỀN ==========
def has_admin_role():
    async def predicate(ctx):
        if ctx.author.id == OWNER_ID or ctx.author.id == ctx.guild.owner_id:
            return True
        if ALLOWED_ROLE_ID is None:
            return False
        role = ctx.guild.get_role(ALLOWED_ROLE_ID)
        if role is None:
            return False
        return role in ctx.author.roles
    return commands.check(predicate)

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

def contains_bad_words(content):
    c = content.lower()
    return any(word in c for word in BAD_WORDS)

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

# ========== SỰ KIỆN ==========
@bot.event
async def on_ready():
    print(f"✅ Bot: {bot.user.name} (ID: {bot.user.id})")
    print(f"👑 Owner bot (bạn) có ID: {OWNER_ID}")
    print("🛡️ Bot sẽ không xóa tin nhắn của bạn, admin, và owner server.")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # === KHÔNG XÓA TIN NHẮN CỦA: OWNER_ID, ADMIN, OWNER SERVER ===
    if message.author.id == OWNER_ID or message.author.guild_permissions.administrator or message.author.id == message.guild.owner_id:
        await bot.process_commands(message)
        return
    # ============================================================

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

        if contains_bad_words(content):
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
    await ctx.send("🤖 Bot bảo vệ đang hoạt động")

@bot.command()
async def dzai(ctx):
    embed = discord.Embed(title="🛡️ Danh sách lệnh", color=discord.Color.green())
    embed.add_field(name="!test", value="Kiểm tra bot", inline=False)
    embed.add_field(name="!set_log_channel #kênh", value="Đặt kênh log", inline=False)
    embed.add_field(name="!set_admin_role @role", value="Đặt role quản trị bot", inline=False)
    embed.add_field(name="!toggle_nuke on/off", value="Bật/tắt chống Nuke", inline=False)
    embed.add_field(name="!toggle_spam on/off", value="Bật/tắt chống spam", inline=False)
    embed.add_field(name="!toggle_raid on/off", value="Bật/tắt chống raid", inline=False)
    embed.add_field(name="!allow_channel #kênh", value="Whitelist kênh gửi link", inline=False)
    embed.add_field(name="!allow_role @role", value="Whitelist role gửi link", inline=False)
    embed.add_field(name="!add_scam_domain <domain>", value="Thêm domain lừa đảo", inline=False)
    embed.add_field(name="!add_badword <từ>", value="Thêm từ chửi bậy", inline=False)
    embed.add_field(name="!add_scam_image_keyword <từ>", value="Thêm từ khóa ảnh scam", inline=False)
    embed.add_field(name="!add_nsfw_keyword <từ>", value="Thêm từ khóa NSFW", inline=False)
    embed.add_field(name="!raid_mode_status", value="Xem trạng thái RAID", inline=False)
    embed.add_field(name="!reset_raid_mode", value="Tắt RAID mode", inline=False)
    embed.add_field(name="!reset_violations @user", value="Reset số lần vi phạm", inline=False)
    embed.add_field(name="!check_violations @user", value="Xem số lần vi phạm", inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.is_owner()
async def set_admin_role(ctx, role: discord.Role):
    global ALLOWED_ROLE_ID
    ALLOWED_ROLE_ID = role.id
    await ctx.send(f"✅ Đã đặt role quản trị bot là {role.mention}. Owner server và ai có role này sẽ dùng được lệnh.")

@bot.command()
@has_admin_role()
async def set_log_channel(ctx, channel: discord.TextChannel = None):
    global log_channel_id
    if channel is None:
        channel = ctx.channel
    log_channel_id = channel.id
    await ctx.send(f"✅ Kênh log: {channel.mention}")

@bot.command()
@has_admin_role()
async def toggle_nuke(ctx, status: str = None):
    global ANTI_NUKE
    if status is None:
        ANTI_NUKE = not ANTI_NUKE
    else:
        ANTI_NUKE = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Nuke: {'BẬT' if ANTI_NUKE else 'TẮT'}")

@bot.command()
@has_admin_role()
async def toggle_spam(ctx, status: str = None):
    global ANTI_SPAM
    if status is None:
        ANTI_SPAM = not ANTI_SPAM
    else:
        ANTI_SPAM = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Spam: {'BẬT' if ANTI_SPAM else 'TẮT'}")

@bot.command()
@has_admin_role()
async def toggle_raid(ctx, status: str = None):
    global ANTI_RAID
    if status is None:
        ANTI_RAID = not ANTI_RAID
    else:
        ANTI_RAID = status.lower() == "on"
    await ctx.send(f"🛡️ Anti-Raid: {'BẬT' if ANTI_RAID else 'TẮT'}")

@bot.command()
@has_admin_role()
async def allow_channel(ctx, channel: discord.TextChannel):
    ALLOWED_CHANNELS.append(channel.id)
    await ctx.send(f"✅ Cho phép link trong {channel.mention}")

@bot.command()
@has_admin_role()
async def allow_role(ctx, role: discord.Role):
    ALLOWED_ROLES.append(role.id)
    await ctx.send(f"✅ Cho phép link với role {role.name}")

@bot.command()
@has_admin_role()
async def add_scam_domain(ctx, domain: str):
    SCAM_DOMAINS.append(domain.lower())
    await ctx.send(f"✅ Đã thêm domain {domain}")

@bot.command()
@has_admin_role()
async def add_badword(ctx, *, word: str):
    BAD_WORDS.append(word.lower())
    await ctx.send(f"✅ Đã thêm từ cấm {word}")

@bot.command()
@has_admin_role()
async def add_scam_image_keyword(ctx, *, keyword: str):
    SCAM_IMAGE_KEYWORDS.append(keyword.lower())
    await ctx.send(f"✅ Thêm từ khóa ảnh scam: {keyword}")

@bot.command()
@has_admin_role()
async def add_nsfw_keyword(ctx, *, keyword: str):
    NSFW_KEYWORDS.append(keyword.lower())
    await ctx.send(f"✅ Thêm từ khóa NSFW: {keyword}")

@bot.command()
@has_admin_role()
async def raid_mode_status(ctx):
    await ctx.send(f"🚨 RAID MODE: {'BẬT' if RAID_MODE else 'TẮT'}")

@bot.command()
@has_admin_role()
async def reset_raid_mode(ctx):
    global RAID_MODE, join_times
    RAID_MODE = False
    join_times.clear()
    await ctx.send("✅ Đã tắt RAID mode và reset bộ đếm.")

@bot.command()
@has_admin_role()
async def reset_violations(ctx, member: discord.Member):
    if member.id in violation_count:
        del violation_count[member.id]
        await ctx.send(f"✅ Reset vi phạm cho {member.mention}")
    else:
        await ctx.send(f"ℹ️ {member.mention} chưa vi phạm lần nào.")

@bot.command()
@has_admin_role()
async def check_violations(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author
    count = violation_count.get(member.id, 0)
    await ctx.send(f"📊 {member.mention} có {count}/5 lần vi phạm.")

# ========== CHẠY BOT ==========
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Không tìm thấy DISCORD_TOKEN trong file .env")
    else:
        keep_alive()
        bot.run(TOKEN)