import discord
from discord.ext import commands
from datetime import timedelta
from collections import defaultdict
import re
import time
import asyncio
import hashlib
import aiohttp
import json
import os
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=">", intents=intents, help_command=None)

# ========== CẤU HÌNH TOÀN CỤC ==========
OWNER_ID = 1075422580833529879   # 👈 THAY BẰNG ID DISCORD CỦA BẠN

SPAM_THRESHOLD = 5
SPAM_WINDOW = 10
EMOJI_SPAM_LIMIT = 5
STICKER_SPAM_LIMIT = 10
STICKER_WINDOW = 5
RAID_THRESHOLD = 10
RAID_WINDOW = 5
MAX_LINE_LENGTH = 50
MEDIA_LIMIT = 5
MEDIA_WINDOW = 10

BAD_WORDS = ["địt","lồn","cặc","đụ","vcl","dm","đmm","cc","cac","duma","ditme","fuck","shit","bitch","đĩ","mẹ kiếp","chết tiệt","mẹ mày","nigga","nigger","nick her"]
SCAM_DOMAINS = ["bit.ly","tinyurl.com","rebrand.ly","discord.gift","steamcommmunity.com","nitro-steam.com","free-discord-nitro.com","trúng thưởng","quà tặng","free nitro","giveaway","mrbeast","beast games"]
SCAM_IMAGE_KEYWORDS = ["mrbeast","mr beast","jj","j.j","giveaway","quà tặng","free nitro","free steam","free gift","trúng thưởng","nhận quà","100% free"]
NSFW_KEYWORDS = ["nsfw","18+","porn","sex","adult","xxx","hentai","dirty","khiêu dâm","người lớn","18plus","sexviet","vlxx","phim sex"]
INVITE_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?discord(?:app)?\.(?:com|gg)/(?:invite/)?([a-zA-Z0-9\-]+)", re.IGNORECASE)

BLACKLISTED_IMAGE_HASHES = set()

# ========== LƯU CẤU HÌNH SERVER ==========
CONFIG_FILE = "guild_config.json"

def load_guild_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_guild_config(data):
    to_save = {}
    for gid_str, cfg in data.items():
        gid = int(gid_str)
        to_save[gid_str] = {
            "ANTI_NUKE": cfg.get("ANTI_NUKE", True),
            "ANTI_SPAM": cfg.get("ANTI_SPAM", True),
            "ANTI_RAID": cfg.get("ANTI_RAID", True),
            "ANTI_IMAGE": cfg.get("ANTI_IMAGE", True),
            "ALLOWED_USERS": cfg.get("ALLOWED_USERS", []),
            "ALLOWED_BAD_WORDS": cfg.get("ALLOWED_BAD_WORDS", []),
            "ALLOWED_BAD_MEMBERS": cfg.get("ALLOWED_BAD_MEMBERS", []),
            "WHITELIST_CHANNELS": cfg.get("WHITELIST_CHANNELS", []),
            "TRANSLATE_CHANNELS": cfg.get("TRANSLATE_CHANNELS", []),
            "log_channel_id": cfg.get("log_channel_id"),
            "RAID_MODE": cfg.get("RAID_MODE", False),
            "join_times": cfg.get("join_times", []),
            "user_messages": dict(cfg.get("user_messages", {})),
            "user_stickers": dict(cfg.get("user_stickers", {})),
            "user_media": dict(cfg.get("user_media", {})),
            "violation_count": dict(cfg.get("violation_count", {})),
            "blacklist": cfg.get("blacklist", [])
        }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, indent=4, ensure_ascii=False)

guild_config = defaultdict(lambda: {
    "ANTI_NUKE": True,
    "ANTI_SPAM": True,
    "ANTI_RAID": True,
    "ANTI_IMAGE": True,
    "ALLOWED_USERS": [],
    "ALLOWED_BAD_WORDS": [],
    "ALLOWED_BAD_MEMBERS": [],
    "WHITELIST_CHANNELS": [],
    "TRANSLATE_CHANNELS": [],
    "log_channel_id": None,
    "RAID_MODE": False,
    "join_times": [],
    "user_messages": defaultdict(list),
    "user_stickers": defaultdict(list),
    "user_media": defaultdict(list),
    "violation_count": defaultdict(int),
    "blacklist": []
})

loaded = load_guild_config()
for gid_str, cfg in loaded.items():
    gid = int(gid_str)
    cfg["user_messages"] = defaultdict(list, cfg["user_messages"])
    cfg["user_stickers"] = defaultdict(list, cfg["user_stickers"])
    cfg["user_media"] = defaultdict(list, cfg["user_media"])
    cfg["violation_count"] = defaultdict(int, cfg["violation_count"])
    guild_config[gid] = cfg

def save_config():
    save_guild_config(guild_config)

# ========== KIỂM TRA QUYỀN ==========
async def check_admin_permission(ctx):
    if ctx.author.id == OWNER_ID or ctx.author.id == ctx.guild.owner_id:
        return True
    if ctx.author.id in guild_config[ctx.guild.id]["ALLOWED_USERS"]:
        return True
    await ctx.send("❌ Bạn không có quyền dùng lệnh này!")
    return False

# ========== LOG ==========
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

# ========== KIỂM TRA NỘI DUNG ==========
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
    for w in BAD_WORDS:
        if w in c and w not in gcfg["ALLOWED_BAD_WORDS"]:
            return True
    return False

def contains_scam(content):
    c = content.lower()
    return any(domain in c for domain in SCAM_DOMAINS) or any(kw in c for kw in SCAM_IMAGE_KEYWORDS)

def contains_nsfw(content):
    c = content.lower()
    urls = re.findall(r'https?://\S+', c)
    for url in urls:
        if any(kw in url for kw in NSFW_KEYWORDS):
            return True
    return False

async def is_allowed_link(ctx, content):
    if ctx.channel.id in guild_config[ctx.guild.id]["WHITELIST_CHANNELS"]:
        return True
    return False

async def handle_violation(user, guild, reason):
    gcfg = guild_config[guild.id]
    gcfg["violation_count"][user.id] += 1
    cur = gcfg["violation_count"][user.id]
    await log_action(guild, f"⚠️ Vi phạm lần {cur}/5", user.mention, moderator=bot.user, reason=reason)
    if cur >= 5:
        try:
            await guild.ban(user, reason=f"5 lần vi phạm: {reason}")
            await log_action(guild, "🔨 ĐÃ BAN", user.mention, moderator=bot.user, reason=reason)
            del gcfg["violation_count"][user.id]
        except:
            pass
    else:
        try:
            await user.timeout(timedelta(hours=1), reason=reason)
            await log_action(guild, "🔇 Auto Mute", user.mention, moderator=bot.user, reason=reason)
        except:
            pass
    save_config()

# ========== AUTO DỊCH ==========
translator = GoogleTranslator(source='auto', target='vi')
async def translate_text(text):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, translator.translate, text)

def detect_language(text):
    try:
        return detect(text)
    except:
        return None

# ========== HÀM ẢNH ==========
async def download_image(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
    except:
        pass
    return None

def compute_md5(data):
    return hashlib.md5(data).hexdigest()

# ========== SỰ KIỆN ==========
@bot.event
async def on_ready():
    print(f"✅ Bot: {bot.user.name} (ID: {bot.user.id})")
    print(f"👑 Owner bot: {OWNER_ID}")
    print("🛡️ Bot bảo vệ đã sẵn sàng (prefix >)")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.guild is None:
        await bot.process_commands(message)
        return

    gcfg = guild_config[message.guild.id]

    # Auto dịch
    if message.content and not message.content.startswith('>'):
        if message.channel.id in gcfg["TRANSLATE_CHANNELS"] and len(message.content.strip()) > 2:
            try:
                lang = detect_language(message.content)
                if lang and lang != 'vi':
                    trans = await translate_text(message.content)
                    if trans and trans.lower() != message.content.lower():
                        await message.reply(f"🌐 **Dịch sang tiếng Việt:** {trans}")
            except:
                pass

    # Miễn trừ ADMIN/OWNER
    if message.author.id == OWNER_ID or message.author.guild_permissions.administrator or message.author.id == message.guild.owner_id:
        await bot.process_commands(message)
        return

    ctx = await bot.get_context(message)
    content = message.content
    user = message.author
    guild = message.guild

    async def punish(reason, mute_hours=1):
        await message.delete()
        await handle_violation(user, guild, reason)

    # Anti ảnh hash
    if gcfg["ANTI_IMAGE"] and message.attachments:
        for att in message.attachments:
            if att.filename.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp')):
                data = await att.read()
                h = compute_md5(data)
                if h in BLACKLISTED_IMAGE_HASHES:
                    await punish(f"Ảnh scam (hash {h[:10]}...)")
                    return

    # Anti scam / nsfw / invite / ping
    if contains_scam(content):
        await punish(f"Scam: {content[:100]}")
        return
    if contains_nsfw(content):
        await punish(f"NSFW: {content[:100]}")
        return
    if INVITE_PATTERN.search(content) and not ctx.channel.id in gcfg["WHITELIST_CHANNELS"]:
        await punish("Gửi link mời Discord không được phép")
        return
    if '@everyone' in content or '@here' in content:
        await punish("Ping @everyone/@here bị cấm", mute_hours=2)
        return

    # Blacklist
    if user.id in gcfg["blacklist"]:
        await punish(f"Blacklist: {content[:50]}")
        return

    # Kênh whitelist
    if ctx.channel.id in gcfg["WHITELIST_CHANNELS"]:
        await bot.process_commands(message)
        return

    # Anti spam
    if gcfg["ANTI_SPAM"]:
        ec = count_emojis(content)
        if ec > EMOJI_SPAM_LIMIT:
            await punish(f"Spam emoji ({ec})")
            return
        if message.stickers:
            now = time.time()
            lst = gcfg["user_stickers"][user.id]
            lst.append(now)
            lst[:] = [t for t in lst if now - t < STICKER_WINDOW]
            if len(lst) > STICKER_SPAM_LIMIT:
                await punish(f"Spam sticker ({len(lst)})")
                return
        if message.attachments or message.embeds:
            now = time.time()
            lst = gcfg["user_media"][user.id]
            lst.append(now)
            lst[:] = [t for t in lst if now - t < MEDIA_WINDOW]
            if len(lst) > MEDIA_LIMIT:
                await punish(f"Spam media ({len(lst)} lần)")
                return
        if contains_bad_words(content, user.id, guild.id):
            await punish(f"Chửi bậy: {content[:50]}")
            return
        if len(content.splitlines()) > MAX_LINE_LENGTH:
            await punish(f"Tin nhắn dài {len(content.splitlines())} dòng")
            return
        now = time.time()
        lst = gcfg["user_messages"][user.id]
        lst.append(now)
        lst[:] = [t for t in lst if now - t < SPAM_WINDOW]
        if len(lst) > SPAM_THRESHOLD:
            await punish(f"Spam tin nhắn ({len(lst)} tin / {SPAM_WINDOW}s)")
            return

    await bot.process_commands(message)

# ========== ANTI-RAID ==========
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
        save_config()

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

# ========== HELP VIEW ĐƠN GIẢN (KHÔNG SELECT MENU) ==========
HELP_PAGES = [
    {
        "title": "🛡️ Bảo vệ & Chống Spam",
        "commands": [
            (">test", "Kiểm tra bot"),
            (">antispam on/off", "Bật/tắt chống spam"),
            (">antinuke on/off", "Bật/tắt anti-nuke"),
            (">antiraid on/off", "Bật/tắt anti-raid"),
            (">whitelist", "Thêm kênh hiện tại vào whitelist (miễn spam, cho link)"),
            (">removewhitelist", "Xóa kênh khỏi whitelist"),
            (">antiimage <link> (hoặc upload)", "Thêm ảnh scam vào blacklist"),
            (">antiimage on/off", "Bật/tắt chức năng chặn ảnh hash"),
            (">add scam keyword <từ>", "Thêm từ khóa scam ảnh"),
            (">add nsfw keyword <từ>", "Thêm từ khóa NSFW"),
            (">raid mode", "Xem trạng thái RAID"),
            (">reset raid mode", "Tắt chế độ RAID"),
        ]
    },
    {
        "title": "👮 Quản trị thành viên",
        "commands": [
            (">mute @user <thời gian>", "Mute user (10m, 2h, 1d)"),
            (">unmute @user", "Gỡ mute"),
            (">kick @user", "Kick"),
            (">ban @user", "Ban"),
            (">unban <user_id>", "Unban"),
            (">clear @user <số lượng>", "Xóa tin nhắn của user (all kênh)"),
            (">blacklist add/remove @user", "Thêm/xóa blacklist"),
            (">violations reset @user", "Reset số lần vi phạm"),
            (">violations check @user", "Xem số lần vi phạm"),
        ]
    },
    {
        "title": "🌐 Tiện ích & Dịch thuật",
        "commands": [
            (">translate on/off", "Bật/tắt auto dịch trong kênh hiện tại"),
            (">scan", "Quét kênh xóa NSFW, invite"),
            (">allowbadword <từ>", "Cho phép từ chửi bậy"),
            (">allow @user", "Cho phép user được chửi bậy"),
            (">remove allowbadword <từ>", "Xóa từ khỏi whitelist"),
            (">remove allow @user", "Xóa user khỏi whitelist"),
        ]
    },
    {
        "title": "🎮 Giải trí (Solo)",
        "commands": [
            (">solo @user <số lượng> <nội dung> [delay=0.5] [emoji]", "Đấu spam"),
            (">stop solo", "Dừng solo"),
        ]
    },
    {
        "title": "⚙️ Cấu hình bot",
        "commands": [
            (">set log #kênh (hoặc không)", "Đặt kênh log (không #kênh = kênh hiện tại)"),
            (">admin user @user", "Thêm admin bot"),
            (">admin remove @user", "Xóa admin"),
            (">list admin users", "Xem danh sách admin"),
        ]
    }
]

class HelpView(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=180)
        self.pages = pages
        self.current_page = 0
        # Nút Previous
        self.prev_button = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="prev")
        self.prev_button.callback = self.prev_callback
        self.add_item(self.prev_button)
        # Nút Next
        self.next_button = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="next")
        self.next_button.callback = self.next_callback
        self.add_item(self.next_button)

    async def prev_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % len(self.pages)
        embed = self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % len(self.pages)
        embed = self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def get_embed(self):
        page = self.pages[self.current_page]
        embed = discord.Embed(title=f"📘 {page['title']}", color=discord.Color.blurple())
        desc_lines = []
        for cmd, desc in page["commands"]:
            desc_lines.append(f"**{cmd}** – {desc}")
        embed.description = "\n".join(desc_lines)
        embed.set_footer(text=f"Trang {self.current_page+1}/{len(self.pages)} • Tổng {sum(len(p['commands']) for p in self.pages)} lệnh • Dùng prefix `>`")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user == self.ctx.author

# ========== LỆNH ==========
@bot.command()
async def test(ctx):
    await ctx.send("🤖 Bot đang hoạt động")

@bot.command(aliases=['help'])
async def _help(ctx):
    view = HelpView(HELP_PAGES)
    view.ctx = ctx
    embed = view.get_embed()
    await ctx.send(embed=embed, view=view)

# ========== CÁC LỆNH CHÍNH ==========
@bot.command()
async def antispam(ctx, status: str):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if status.lower() == "on":
        gcfg["ANTI_SPAM"] = True
        await ctx.send("🛡️ Anti-Spam: BẬT")
    elif status.lower() == "off":
        gcfg["ANTI_SPAM"] = False
        await ctx.send("🛡️ Anti-Spam: TẮT")
    else:
        await ctx.send("❌ Cú pháp: `>antispam on/off`")
        return
    save_config()

@bot.command()
async def antinuke(ctx, status: str):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if status.lower() == "on":
        gcfg["ANTI_NUKE"] = True
        await ctx.send("🛡️ Anti-Nuke: BẬT")
    elif status.lower() == "off":
        gcfg["ANTI_NUKE"] = False
        await ctx.send("🛡️ Anti-Nuke: TẮT")
    else:
        await ctx.send("❌ Cú pháp: `>antinuke on/off`")
        return
    save_config()

@bot.command()
async def antiraid(ctx, status: str):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if status.lower() == "on":
        gcfg["ANTI_RAID"] = True
        await ctx.send("🛡️ Anti-Raid: BẬT")
    elif status.lower() == "off":
        gcfg["ANTI_RAID"] = False
        await ctx.send("🛡️ Anti-Raid: TẮT")
    else:
        await ctx.send("❌ Cú pháp: `>antiraid on/off`")
        return
    save_config()

@bot.command()
async def whitelist(ctx):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if ctx.channel.id in gcfg["WHITELIST_CHANNELS"]:
        await ctx.send("ℹ️ Kênh này đã được whitelist.")
        return
    gcfg["WHITELIST_CHANNELS"].append(ctx.channel.id)
    await ctx.send(f"✅ Đã thêm kênh {ctx.channel.mention} vào whitelist")
    save_config()

@bot.command()
async def removewhitelist(ctx):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if ctx.channel.id not in gcfg["WHITELIST_CHANNELS"]:
        await ctx.send("ℹ️ Kênh này chưa được whitelist.")
        return
    gcfg["WHITELIST_CHANNELS"].remove(ctx.channel.id)
    await ctx.send(f"✅ Đã xóa kênh {ctx.channel.mention} khỏi whitelist")
    save_config()

@bot.command()
async def antiimage(ctx, param: str = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if param is None:
        await ctx.send("❌ Cú pháp: `>antiimage on/off` hoặc `>antiimage <link>` (kèm file)")
        return
    if param.lower() in ["on", "off"]:
        state = param.lower() == "on"
        gcfg["ANTI_IMAGE"] = state
        await ctx.send(f"🛡️ Chức năng chặn ảnh hash: {'BẬT' if state else 'TẮT'}")
        save_config()
        return
    data = None
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        if att.filename.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp')):
            data = await att.read()
        else:
            await ctx.send("❌ File không phải ảnh")
            return
    else:
        url = param
        data = await download_image(url)
        if not data:
            await ctx.send("❌ Không tải được ảnh từ link")
            return
    h = compute_md5(data)
    BLACKLISTED_IMAGE_HASHES.add(h)
    await ctx.send(f"✅ Đã thêm ảnh vào blacklist. Hash: `{h[:10]}...`")

@bot.command()
async def add(ctx, type: str, *, keyword: str):
    if not await check_admin_permission(ctx): return
    if type.lower() == "scam":
        SCAM_IMAGE_KEYWORDS.append(keyword.lower())
        await ctx.send(f"✅ Đã thêm từ khóa scam ảnh: {keyword}")
    elif type.lower() == "nsfw":
        NSFW_KEYWORDS.append(keyword.lower())
        await ctx.send(f"✅ Đã thêm từ khóa NSFW: {keyword}")
    else:
        await ctx.send("❌ Loại không hợp lệ. Dùng `>add scam keyword <từ>` hoặc `>add nsfw keyword <từ>`")

@bot.command()
async def raid(ctx, mode=None):
    if not await check_admin_permission(ctx): return
    if mode is None:
        gcfg = guild_config[ctx.guild.id]
        await ctx.send(f"🚨 RAID MODE: {'BẬT' if gcfg['RAID_MODE'] else 'TẮT'}")
    else:
        await ctx.send("❌ Cú pháp: `>raid mode` để xem, `>reset raid mode` để tắt")

@bot.command()
async def reset(ctx, mode=None):
    if not await check_admin_permission(ctx): return
    if mode and mode.lower() == "raid mode":
        gcfg = guild_config[ctx.guild.id]
        gcfg["RAID_MODE"] = False
        gcfg["join_times"].clear()
        await ctx.send("✅ Đã tắt RAID mode và reset bộ đếm")
        save_config()
    else:
        await ctx.send("❌ Cú pháp: `>reset raid mode`")

@bot.command()
async def scan(ctx):
    if not await check_admin_permission(ctx): return
    await ctx.send(f"🔍 Đang quét kênh này...")
    deleted = 0
    async for msg in ctx.channel.history(limit=500):
        if msg.author == bot.user: continue
        c = msg.content.lower()
        if contains_nsfw(c) or INVITE_PATTERN.search(c) or contains_bad_words(c, msg.author.id, ctx.guild.id):
            try:
                await msg.delete()
                deleted += 1
                await asyncio.sleep(0.5)
            except:
                pass
    await ctx.send(f"✅ Đã xóa {deleted} tin nhắn vi phạm (NSFW, link invite, spam).")

@bot.command()
async def clear(ctx, member: discord.Member, amount: int):
    if not await check_admin_permission(ctx): return
    if amount <= 0:
        await ctx.send("❌ Số lượng tin nhắn phải lớn hơn 0")
        return
    total = 0
    for channel in ctx.guild.text_channels:
        try:
            async for msg in channel.history(limit=amount):
                if msg.author.id == member.id:
                    try:
                        await msg.delete()
                        total += 1
                        await asyncio.sleep(0.2)
                    except:
                        pass
        except:
            continue
    await ctx.send(f"✅ Đã xóa {total} tin nhắn gần nhất của {member.mention} (giới hạn {amount} tin/kênh).")

@bot.command()
async def blacklist(ctx, action: str, user: discord.User = None):
    if not await check_admin_permission(ctx): return
    if action.lower() not in ["add", "remove"]:
        await ctx.send("❌ Hành động chỉ có `add` hoặc `remove`")
        return
    if not user:
        await ctx.send("❌ Tag user")
        return
    gcfg = guild_config[ctx.guild.id]
    if action.lower() == "add":
        if user.id in gcfg["blacklist"]:
            await ctx.send("ℹ️ User đã có trong blacklist")
            return
        gcfg["blacklist"].append(user.id)
        await ctx.send(f"✅ Đã thêm {user.mention} vào blacklist (mọi tin nhắn sẽ bị xóa và muted)")
    else:
        if user.id not in gcfg["blacklist"]:
            await ctx.send("ℹ️ User không có trong blacklist")
            return
        gcfg["blacklist"].remove(user.id)
        await ctx.send(f"✅ Đã xóa {user.mention} khỏi blacklist")
    save_config()

@bot.command()
async def violations(ctx, action: str, member: discord.Member = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if action.lower() == "reset":
        if not member:
            await ctx.send("❌ Cần tag user: `>violations reset @user`")
            return
        if member.id in gcfg["violation_count"]:
            del gcfg["violation_count"][member.id]
            await ctx.send(f"✅ Reset vi phạm cho {member.mention}")
            save_config()
        else:
            await ctx.send(f"ℹ️ {member.mention} chưa vi phạm lần nào")
    elif action.lower() == "check":
        target = member or ctx.author
        count = gcfg["violation_count"].get(target.id, 0)
        await ctx.send(f"📊 {target.mention} có {count}/5 lần vi phạm")
    else:
        await ctx.send("❌ Hành động chỉ có `reset` hoặc `check`")

@bot.command()
async def translate(ctx, action: str):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if action.lower() == "on":
        if ctx.channel.id in gcfg["TRANSLATE_CHANNELS"]:
            await ctx.send("ℹ️ Kênh đã được bật auto dịch")
            return
        gcfg["TRANSLATE_CHANNELS"].append(ctx.channel.id)
        await ctx.send(f"✅ Đã bật auto dịch trong kênh {ctx.channel.mention}")
        save_config()
    elif action.lower() == "off":
        if ctx.channel.id not in gcfg["TRANSLATE_CHANNELS"]:
            await ctx.send("ℹ️ Kênh chưa được bật auto dịch")
            return
        gcfg["TRANSLATE_CHANNELS"].remove(ctx.channel.id)
        await ctx.send(f"✅ Đã tắt auto dịch trong kênh {ctx.channel.mention}")
        save_config()
    else:
        await ctx.send("❌ Cú pháp: `>translate on/off` trong kênh cần cấu hình")

@bot.command()
async def allowbadword(ctx, *, word: str):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    w = word.lower()
    if w in gcfg["ALLOWED_BAD_WORDS"]:
        await ctx.send("ℹ️ Từ đã được cho phép")
        return
    gcfg["ALLOWED_BAD_WORDS"].append(w)
    await ctx.send(f"✅ Cho phép từ '{word}' chửi bậy trong server")
    save_config()

@bot.command()
async def allow(ctx, user: discord.Member):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if user.id in gcfg["ALLOWED_BAD_MEMBERS"]:
        await ctx.send("ℹ️ User đã được cho phép")
        return
    gcfg["ALLOWED_BAD_MEMBERS"].append(user.id)
    await ctx.send(f"✅ Cho phép {user.mention} chửi bậy trong server")
    save_config()

@bot.command()
async def remove(ctx, type: str, *, target: str = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if type.lower() == "allowbadword":
        if not target:
            await ctx.send("❌ Cú pháp: `>remove allowbadword <từ>`")
            return
        w = target.lower()
        if w not in gcfg["ALLOWED_BAD_WORDS"]:
            await ctx.send("ℹ️ Từ không có trong danh sách")
            return
        gcfg["ALLOWED_BAD_WORDS"].remove(w)
        await ctx.send(f"✅ Đã xóa từ '{target}' khỏi whitelist")
        save_config()
    elif type.lower() == "allow":
        if not target:
            await ctx.send("❌ Cú pháp: `>remove allow @user`")
            return
        try:
            user = await commands.UserConverter().convert(ctx, target)
        except:
            await ctx.send("❌ Không tìm thấy user")
            return
        if user.id not in gcfg["ALLOWED_BAD_MEMBERS"]:
            await ctx.send("ℹ️ User không có trong danh sách")
            return
        gcfg["ALLOWED_BAD_MEMBERS"].remove(user.id)
        await ctx.send(f"✅ Đã xóa {user.mention} khỏi danh sách cho phép chửi bậy")
        save_config()
    else:
        await ctx.send("❌ Cú pháp: `>remove allowbadword <từ>` hoặc `>remove allow @user`")

solo_tasks = {}
@bot.command()
async def solo(ctx, target: discord.Member, amount: int, *, content_with_options: str = ""):
    if not await check_admin_permission(ctx): return
    if amount > 9999:
        return await ctx.send("❌ Số lượng tối đa 9999")
    if amount <= 0:
        return await ctx.send("❌ Số lượng >0")
    delay = 0.5
    content = content_with_options
    emoji = ""
    dm = re.search(r'delay\s*=\s*([\d.]+)', content_with_options, re.I)
    if dm:
        delay = float(dm.group(1))
        content = re.sub(r'delay\s*=\s*[\d.]+', '', content_with_options, flags=re.I).strip()
    words = content.rsplit(' ', 1)
    if len(words) == 2 and re.search(r'[\U00010000-\U0010FFFF]', words[1]):
        content, emoji = words
    if not content:
        content = "spam"
    msg = f"{content} {emoji}".strip()
    if ctx.channel.id in solo_tasks and not solo_tasks[ctx.channel.id].done():
        solo_tasks[ctx.channel.id].cancel()
        await ctx.send("⏹️ Hủy solo cũ")
        await asyncio.sleep(0.5)
    await ctx.send(f"🎮 Bắt đầu solo {amount} lần với {target.mention}! (delay {delay}s, nd: {msg})")
    async def task():
        try:
            for i in range(amount):
                await ctx.send(f"{target.mention} {msg}")
                await asyncio.sleep(delay)
            await ctx.send(f"✅ Solo xong {amount} lần với {target.mention}!")
        except asyncio.CancelledError:
            await ctx.send(f"⏹️ Solo dừng lại")
        finally:
            solo_tasks.pop(ctx.channel.id, None)
    solo_tasks[ctx.channel.id] = asyncio.create_task(task())

@bot.command()
async def stop(ctx, target=None):
    if target and target.lower() == "solo" or not target:
        if ctx.channel.id in solo_tasks and not solo_tasks[ctx.channel.id].done():
            solo_tasks[ctx.channel.id].cancel()
            await ctx.send("⏹️ Đã yêu cầu dừng solo")
        else:
            await ctx.send("❌ Không có solo trong kênh này")
    else:
        await ctx.send("❌ Cú pháp: `>stop solo`")

@bot.command()
async def set(ctx, log_channel: discord.TextChannel = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if log_channel is None:
        log_channel = ctx.channel
    gcfg["log_channel_id"] = log_channel.id
    await ctx.send(f"✅ Kênh log: {log_channel.mention}")
    save_config()

@bot.command()
async def admin(ctx, action: str, user: discord.User = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if action.lower() == "user":
        if not user:
            await ctx.send("❌ Cần tag user: `>admin user @user`")
            return
        if user.id in gcfg["ALLOWED_USERS"]:
            await ctx.send("ℹ️ User đã là admin")
            return
        gcfg["ALLOWED_USERS"].append(user.id)
        await ctx.send(f"✅ Đã thêm {user.mention} vào danh sách admin")
        save_config()
    elif action.lower() == "remove":
        if not user:
            await ctx.send("❌ Cần tag user: `>admin remove @user`")
            return
        if user.id not in gcfg["ALLOWED_USERS"]:
            await ctx.send("ℹ️ User không phải admin")
            return
        gcfg["ALLOWED_USERS"].remove(user.id)
        await ctx.send(f"✅ Đã xóa {user.mention} khỏi danh sách admin")
        save_config()
    else:
        await ctx.send("❌ Cú pháp: `>admin user @user` hoặc `>admin remove @user`")

@bot.command()
async def list(ctx, type: str = None):
    if not await check_admin_permission(ctx): return
    gcfg = guild_config[ctx.guild.id]
    if type and type.lower() == "admin" and ctx.invoked_with == "list":
        if not gcfg["ALLOWED_USERS"]:
            await ctx.send("📃 Chưa có user nào được thêm")
            return
        mentions = []
        for uid in gcfg["ALLOWED_USERS"]:
            try:
                u = await bot.fetch_user(uid)
                mentions.append(u.mention)
            except:
                mentions.append(f"<@{uid}>")
        await ctx.send(f"📋 **Admin users:** {', '.join(mentions)}")
    else:
        await ctx.send("❌ Cú pháp: `>list admin users`")

# ========== CHẠY BOT ==========
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Thiếu TOKEN")
    else:
        bot.run(TOKEN)
