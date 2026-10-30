#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""银川科技学院强智教务系统 CLI

用法:
  jw.py login                      登录并保存 session
  jw.py alive                      检查 session 是否有效
  jw.py keepalive                  保活一次（6字节请求）
  jw.py info                       学籍卡片
  jw.py grades [--gpa] [--term X]  成绩单
  jw.py timetable [学期]            课表
  jw.py exams [学期]                考试安排
  jw.py progress                   对照培养方案的学分完成度
  jw.py rounds                     选课轮次
  jw.py grab --at HH:MM:SS --want "课A,课B"
  jw.py grab --at HH:MM:SS --credit 2.0
"""
import sys, os, re, time, json, base64, html, argparse
from datetime import datetime

try:
    import requests
except ImportError:
    sys.stderr.write("""
  ❌ 缺少依赖 requests

  请先安装：
      pip install requests

  如果 pip 不可用，可以试：
      python3 -m pip install --user requests
      pip3 install requests

  装好后重新运行本命令。
""")
    sys.exit(2)

BASE  = "http://jw.ycust.com/yckjxy_jsxsd"
# 会话文件位置：优先用工作区，取不到就退回脚本所在目录
def _workdir():
    for d in (os.environ.get("WORKSPACE_DIR"), "/workspace", os.path.expanduser("~")):
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return os.path.dirname(os.path.abspath(__file__))

RAW   = False          # --raw 时显示身份证等敏感字段
WD    = _workdir()
CK    = os.path.join(WD, ".jw_session.txt")
UA    = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# 凭据不落盘、不读环境变量。首次使用时必须由用户当场提供。
CRED_FILE = os.path.join(WD, ".jw_cred_disabled")   # 仅作存在性检测，永不写入

# ---------------- 基础设施 ----------------

def dec(r):
    for e in ("utf-8", "gbk", "gb18030"):
        try: return r.content.decode(e)
        except Exception: pass
    return r.content.decode("utf-8", "ignore")

def strip(t):
    t = re.sub(r"<script.*?</script>", "", t, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "\x01", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"[ \t\r]+", " ", t)

def newsess():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                      "Connection": "keep-alive"})
    return s

def save_ck(s):
    with open(CK, "w", encoding="utf-8") as f:
        for c in s.cookies:
            f.write(f"{c.name}\t{c.value}\n")

def load_ck(s):
    try:
        for line in open(CK, encoding="utf-8"):
            p = line.strip().split("\t")
            if len(p) >= 2:
                s.cookies.set(p[0], p[1], domain="jw.ycust.com", path="/")
        return True
    except Exception:
        return False

def logged_in(s, tries=3):
    """检查登录态。高峰期单次请求容易超时，重试几次再判定"""
    for i in range(tries):
        try:
            r = s.get(BASE + "/framework/xsMain.jsp", timeout=20)
            t = dec(r)
            if "学生个人中心" in t:
                return True
            if "用户登录" in t or len(t) < 6000:
                # 明确的未登录
                return False
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(1)
    return False

def ask_credentials(user=None, pwd=None):
    """首次使用必须由用户提供账号密码。不接受任何硬编码/环境变量兜底。"""
    if not user or not pwd:
        print()
        print("=" * 46)
        print("  银川科技学院教务系统 — 需要登录")
        print("=" * 46)
        print("  请提供你的学号与密码（仅本次会话使用，不写入磁盘）")
        print()
        try:
            if not user:
                user = input("  学号: ").strip()
            if not pwd:
                try:
                    import getpass, sys as _s
                    if _s.stdin.isatty():
                        pwd = getpass.getpass("  密码: ").strip()
                    else:
                        pwd = input("  密码: ").strip()
                except Exception:
                    pwd = input("  密码: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return None, None
    if not user or not pwd:
        return None, None
    return user, pwd


def check_net():
    """登录前先探一次网络，避免把网络问题报成密码错误"""
    try:
        s = requests.get(BASE + "/", timeout=8)
        return True
    except requests.exceptions.ConnectTimeout:
        print("\n❌ 连接教务系统超时。可能原因：")
        print("   · 当前网络不通（校园网/VPN 未连接）")
        print("   · 教务系统维护中")
        print("   请确认能打开 http://jw.ycust.com/yckjxy_jsxsd/")
        return False
    except requests.exceptions.ConnectionError:
        print("\n❌ 无法连接教务系统 jw.ycust.com。请检查网络。")
        return False
    except Exception as e:
        print(f"\n❌ 网络异常：{type(e).__name__}")
        return False


def do_login(s, user=None, pwd=None, tries=15, quiet=False):
    """登录。
    关键: allow_redirects=False —— 只要拿到 302 就说明凭证正确、cookie 已下发。
    不要跟随跳转到 xsMain.jsp，那一步在高峰期极易 502，会把成功拖成失败。
    """
    user, pwd = ask_credentials(user, pwd)
    if not user or not pwd:
        print("❌ 未提供凭据，无法登录")
        return False
    enc = (base64.b64encode(user.encode()).decode() + "%%%" +
           base64.b64encode(pwd.encode()).decode())
    try:
        s.get(BASE + "/", timeout=8)
    except Exception:
        pass

    for attempt in range(tries):
        try:
            r = s.post(BASE + "/xk/LoginToXk",
                       data={"encoded": enc, "jzmmid": "1"},
                       headers={"Referer": BASE + "/"},
                       allow_redirects=False, timeout=15)
            # 302 = 登录成功（凭证正确，cookie 已下发）
            if r.status_code == 302:
                loc = r.headers.get("Location", "")
                if "xsMain" in loc or loc.count("/") > 0:
                    save_ck(s)
                    globals()["_CUR_UID"] = user
                    if attempt and not quiet:
                        print(f"  （第 {attempt+1} 次尝试成功）")
                    return True
        except Exception:
            pass
        time.sleep(0.8)
    return False


def ensure(s):
    """复用 session，失效才登录"""
    if load_ck(s) and logged_in(s):
        return True
    return do_login(s)

def keepalive(s):
    try:
        return s.get(BASE + "/framework/blankPage.jsp", timeout=8).status_code == 200
    except Exception:
        return False

# ---------------- 表格解析 ----------------

def parse_table(t):
    """返回 [dict], 表头作为 key"""
    rows, heads = [], []
    for tb in re.findall(r"<table[^>]*>(.*?)</table>", t, flags=re.S | re.I):
        h = [x.strip() for x in strip(re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S)[0]).split("\x01")] \
            if "<tr" in tb else []
        h = [x for x in h if x]
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tb, flags=re.S | re.I)
        tmp = []
        for tr in trs:
            tds = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)
            if not tds: continue
            cells = [re.sub(r"\s+", " ", strip(x)).strip() for x in tds]
            if any(cells): tmp.append(cells)
        if len(tmp) >= 2 and h and len(h) == len(tmp[0]):
            for c in tmp[1:]:
                rows.append(dict(zip(h, c)))
    return rows

# ---------------- 成绩行列对齐 ----------------
# 表头 12 列：序号 开课学期 课程编号 课程名称 成绩 成绩标志 学分 总学时 绩点 考核方式 课程属性 课程性质
# 数据行通常 11 列：因为「成绩标志」为空时被过滤掉。直接按位置取值会整体错位，
# 所以这里用「从右往左」定位稳定字段，避免列数变化导致字段错乱。

def align_grade_row(cs):
    """把一行单元格对齐成 dict，失败返回 None"""
    if not cs or not cs[0].isdigit() or len(cs) < 9:
        return None
    # 右侧固定：课程属性、考核方式、绩点、总学时、学分
    # 从后往前：[-1]=课程性质 [-2]=课程属性 [-3]=考核方式 [-4]=绩点 [-5]=总学时 [-6]=学分
    if len(cs) >= 11:
        mod, attr, exam = cs[-1], cs[-2], cs[-3]
        gpa, hours, credit = cs[-4], cs[-5], cs[-6]
        head = cs[:-6]          # 序号 学期 课号 课程名 成绩 [成绩标志]
    else:
        # 兼容 9~10 列的简化表
        mod = cs[-1] if len(cs) >= 10 else ""
        attr = cs[-2] if len(cs) >= 10 else ""
        exam, gpa, hours, credit = "", cs[-3], cs[-4], cs[-5]
        head = cs[:-5]
    if len(head) < 5:
        return None
    return {"term": head[1], "code": head[2], "name": head[3],
            "score": head[4], "credit": credit, "hours": hours,
            "gpa": gpa, "exam": exam, "attr": attr, "mod": mod}


def s_get(url, timeout=30):
    return dec(G_S.get(BASE + url, timeout=timeout))


def s_post(url, data, timeout=30):
    return dec(G_S.post(BASE + url, data=data or {}, timeout=timeout))


# ---------------- 各功能 ----------------

def cmd_doctor(s=None):
    """自检：环境、依赖、网络、路径"""
    import platform
    ok = True
    print("  环境自检")
    print("  " + "-" * 42)
    print(f"  python   {platform.python_version()}  ({platform.system()})")
    print(f"  工作目录 {WD}")
    print(f"  会话文件 {CK}")
    print(f"  可写     {'✅' if os.access(WD, os.W_OK) else '❌ 无写权限'}")

    try:
        import requests as _r
        print(f"  requests {_r.__version__}  ✅")
    except ImportError:
        print("  requests   ❌ 未安装，请执行: pip install requests")
        ok = False

    # 网络
    for host, label in ((BASE, "教务系统"), ("appdmkj.5idream.net", "（备用）")):
        try:
            import requests as _r
            r = _r.get(host + "/", timeout=8, allow_redirects=True)
            print(f"  连通性   {label} HTTP {r.status_code}  ✅")
            break
        except Exception as e:
            print(f"  连通性   {label} ❌ {type(e).__name__}")
            ok = False

    # 技能完整性
    here = os.path.dirname(os.path.abspath(__file__))
    need = [os.path.join(here, "jw.py"),
            os.path.join(os.path.dirname(here), "SKILL.md"),
            os.path.join(os.path.dirname(here), "references", "api.md")]
    for p in need:
        print(f"  {'✅' if os.path.exists(p) else '❌'} {os.path.relpath(p, os.path.dirname(here))}")
        if not os.path.exists(p): ok = False

    # 目录嵌套检测：SKILL.md 所在目录名应等于技能名
    skilldir = os.path.dirname(here)
    base = os.path.basename(os.path.dirname(skilldir))
    if base == "ycust-jwxt" and os.path.basename(skilldir) == "ycust-jwxt":
        print("  ⚠️  检测到目录嵌套：.../ycust-jwxt/ycust-jwxt/SKILL.md")
        print("     应把内层内容上移一层，或在 clone 时指定完整目标路径")
        ok = False

    print()
    if ok:
        print("  ✅ 一切正常，可以直接使用")
    else:
        print("  ⚠️  存在上述问题，请先修复")
    return 0 if ok else 1


# 学籍卡片里的字段名（用于识别「这是标签不是值」）
_INFO_KEYS = {
    "院系", "专业", "学制", "班级", "学号", "姓名", "性别", "姓名拼音",
    "出生日期", "婚否", "本人电话", "专业方向", "政治面貌", "籍贯",
    "入党团时间", "民族", "学习形式", "学习层次", "外语种类",
    "家庭现住址", "下车火车站", "邮政编码", "家庭电话", "联系人",
    "入学日期", "毕业日期", "入学考号", "身份证编号", "考生号",
}
# 这些是分节标题，遇到就停
_INFO_SECTIONS = {"学 习 及 工 作 简 历", "家 庭 成 员 情 况",
                  "学 籍 变 动 情 况", "奖 励 情 况", "处 分 情 况", "学 籍 卡 片"}


def cmd_info(s):
    """学籍卡片 —— 解析成键值对"""
    t = dec(s.get(BASE + "/grxx/xsxx", timeout=25))

    # 只取含「学籍卡片」的那张表
    tb = None
    for cand in re.findall(r"<table[^>]*>.*?</table>", t, flags=re.S | re.I):
        if "姓名" in cand and ("院系" in cand or "学号" in cand):
            tb = cand
            break
    if not tb:
        print("  学籍卡片解析失败（页面结构可能变了）")
        return

    info = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tb, flags=re.S | re.I):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
                 for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        cells = [c.replace("\xa0", " ").strip() for c in cells]

        # 停在各分节标题
        if any(re.sub(r"\s+", "", c) in _INFO_SECTIONS for c in cells if c):
            if cells[0] and re.sub(r"\s+", "", cells[0]) in _INFO_SECTIONS:
                break

        # 「院系：xxx」这种自带冒号的形式
        for c in cells:
            for m in re.finditer(r"([\u4e00-\u9fa5]{2,8})\s*[：:]\s*([^：:]*)", c):
                k, v = m.group(1), m.group(2).strip()
                if k in _INFO_KEYS and v:
                    info[k] = v

        # 键值成对：标签格后面跟值格
        i = 0
        while i < len(cells):
            c = cells[i]
            key = re.sub(r"\s+", "", c)
            if key in _INFO_KEYS and i + 1 < len(cells):
                val = cells[i + 1].strip()
                if val and re.sub(r"\s+", "", val) not in _INFO_KEYS and key not in info:
                    info[key] = val
                i += 2
            else:
                i += 1

    if not info:
        print("  未解析到字段")
        return

    order = ["院系", "专业", "学制", "班级", "学号", "姓名", "性别", "姓名拼音",
             "出生日期", "民族", "政治面貌", "籍贯", "婚否", "入学日期", "毕业日期",
             "学习层次", "学习形式", "外语种类", "入党团时间", "专业方向",
             "本人电话", "家庭电话", "联系人", "家庭现住址", "下车火车站",
             "邮政编码", "入学考号"]

    # 敏感字段默认打码，加 --raw 才显示完整
    SENSITIVE = {"身份证编号", "入学考号", "本人电话", "家庭电话", "邮政编码", "家庭现住址"}

    def mask(k, v):
        # 非敏感字段、或显式 --raw，都原样返回
        if RAW or k not in SENSITIVE:
            return v
        if k in ("身份证编号", "本人电话", "家庭电话") and len(v) > 8:
            return v[:4] + "*" * (len(v) - 8) + v[-4:]
        return v

    for k in order:
        if k in info:
            print(f"  {k:<8}{mask(k, info[k])}")
    for k, v in info.items():
        if k not in order:
            print(f"  {k:<8}{mask(k, v)}")


def cmd_grades(s, gpa=False, term=None):
    data = {"kksj": term or "", "kcxz": "", "kcmc": "", "xsfs": "all"}
    r = s.post(BASE + "/kscj/cjcx_list", data=data, timeout=25)
    t = dec(r)
    recs = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, flags=re.S | re.I):
        cs = [re.sub(r"\s+", " ", strip(x)).strip()
              for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        cs = [c for c in cs if c]
        if cs and cs[0].isdigit():
            r_ = align_grade_row(cs)
            if r_: recs.append(r_)
    # ★ 去重：同一 学期+课号 取绩点最高
    best = {}
    for r_ in recs:
        k = (r_["term"], r_["code"])
        try: g = float(r_["gpa"])
        except Exception: g = -1
        if k not in best or g > best[k][0]:
            best[k] = (g, r_)
    eff = [v[1] for v in best.values()]

    if not gpa:
        print(f"{'学期':<12}{'课程':<26}{'成绩':>6}{'学分':>6}{'绩点':>6}")
        for r_ in sorted(eff, key=lambda x: (x["term"], x["name"])):
            print(f"{r_['term']:<12}{r_['name'][:24]:<26}{r_['score']:>6}{r_['credit']:>6}{r_['gpa']:>6}")
        print(f"\n共 {len(eff)} 门（原始 {len(recs)} 条，去重 {len(recs)-len(eff)} 条重复/重修）")
        return

    def num(x):
        try: return float(x)
        except Exception: return 0.0
    terms = sorted({x["term"] for x in eff})
    tc = tg = 0.0
    print(f"{'学期':<14}{'门数':>6}{'学分':>8}{'加权绩点':>10}")
    for tm in terms:
        rs = [x for x in eff if x["term"] == tm]
        c  = sum(num(x["credit"]) for x in rs)
        cg = sum(num(x["credit"]) * num(x["gpa"]) for x in rs)
        tc += c; tg += cg
        print(f"{tm:<14}{len(rs):>6}{c:>8.1f}{(cg/c if c else 0):>10.3f}")
    print(f"{'合计':<14}{len(eff):>6}{tc:>8.1f}{(tg/tc if tc else 0):>10.3f}")

def cmd_timetable(s, term=None):
    """课表 —— 解析成「星期 × 节次」二维表
    页面结构: table#kbtable，首行 th 是星期一到星期日，
    每行首个 th 是节次（第1.2节…），其余 td 是课程格子。
    """
    url = BASE + "/xskb/xskb_list.do"
    if term:
        url += "?xnxq01id=" + term
    t = dec(s.get(url, timeout=25))

    tb = re.search(r'<table[^>]*id="kbtable"[^>]*>(.*?)</table>', t, re.S | re.I)
    if not tb:
        if "未查询到数据" in t:
            print("  没有课表数据（可能该学期无课，或学期参数不对）")
        else:
            print("  课表解析失败，请确认学期格式如 2026-2027-1")
        return
    html_tb = tb.group(1)

    # 星期表头
    head = re.search(r"<tr[^>]*>(.*?)</tr>", html_tb, re.S | re.I)
    days = [re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", x))
            for x in re.findall(r"<th[^>]*>(.*?)</th>", head.group(1), re.S | re.I)]
    days = [d for d in days if d and d != "&nbsp;"]
    if not days:
        days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html_tb, re.S | re.I)[1:]
    grid = []           # [(节次, {星期索引: [课程...]})]
    for tr in rows:
        ths = re.findall(r"<th[^>]*>(.*?)</th>", tr, re.S | re.I)
        period = re.sub(r"\s+|&nbsp;", "", re.sub(r"<[^>]+>", "", ths[0])) if ths else ""
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        cell_map = {}
        for di, td in enumerate(tds):
            divs = re.findall(r'<div[^>]*class="kbcontent1"[^>]*>(.*?)</div>', td, re.S | re.I)
            items = []
            for b in divs:
                for fp in (re.compile(r"<font[^>]*title='([^']+)'[^>]*>(.*?)</font>", re.S | re.I),
                           re.compile(r'<font[^>]*title="([^"]+)"[^>]*>(.*?)</font>', re.S | re.I)):
                    for m in fp.finditer(b):
                        b = b.replace(m.group(0), chr(1) + m.group(1) + chr(2) + m.group(2))
                b = re.sub(r"<br\s*/?>", "\n", b, flags=re.I)
                lines = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
                         for x in b.split("\n")]
                lines = [x.replace(chr(1), "").replace(chr(2), ": ") for x in lines if x and x != "\xa0"]
                # 拆开同一格里多门课（用 --- 分隔）
                cur = []
                for x in lines:
                    if re.match(r"^-{5,}$", x):
                        if cur: items.append(cur); cur = []
                    else:
                        cur.append(x)
                if cur: items.append(cur)
            if items:
                cell_map[di] = items
        if cell_map or period:
            grid.append((period, cell_map))

    if not grid:
        print("  课表为空")
        return

    total = sum(len(v) for _, cm in grid for v in cm.values())
    print(f"  {term or '本学期'} · 共 {total} 个课程时段\n")

    # 按星期输出
    for di, day in enumerate(days):
        slots = []
        for period, cm in grid:
            if di in cm:
                for item in cm[di]:
                    slots.append((period, item))
        if not slots:
            continue
        print(f"  【{day}】")
        for period, item in slots:
            name = item[0]
            room, weeks = "", ""
            others = []
            for x in item[1:]:
                if "教室" in x:
                    room = x.split(": ", 1)[-1]
                elif "周次" in x:
                    weeks = x.split(": ", 1)[-1]
                    # 去掉开头的班级名，只留周次
                    m = re.search(r"(\d[\d,\-]*(?:\(全部\))?)$", weeks)
                    if m: weeks = m.group(1)
                else:
                    others.append(x)
            print(f"    {period:<10}{name}")
            info = "  ".join(v for v in (weeks, room) if v)
            if info:
                print(f"    {'':<10}{info}")
            for o in others:
                print(f"    {'':<10}{o}")
        print()


def cmd_hours(s, term=None):
    """学时统计 —— 按课程/按学期汇总"""
    r = s.post(BASE + "/kscj/cjcx_list",
               data={"kksj": term or "", "kcxz": "", "kcmc": "", "xsfs": "all"}, timeout=25)
    t = dec(r)
    recs = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, flags=re.S | re.I):
        cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
              for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        cs = [c for c in cs if c]
        r_ = align_grade_row(cs)
        if r_: recs.append(r_)
    # 去重：同课号取一次
    best = {}
    for r_ in recs:
        k = (r_["term"], r_["code"])
        try: g = float(r_["gpa"])
        except Exception: g = -1
        if k not in best or g > best[k][0]:
            best[k] = (g, r_)
    eff = [v[1] for v in best.values()]
    if not eff:
        print("  没有成绩记录")
        return

    def num(x):
        try: return float(x)
        except Exception: return 0.0

    print(f"{'学期':<14}{'课程':<26}{'学分':>6}{'学时':>6}")
    print("-" * 52)
    terms = sorted({x["term"] for x in eff})
    grand = 0.0
    for tm in terms:
        rs = [x for x in eff if x["term"] == tm]
        sub = 0.0
        for x in rs:
            h = num(x["hours"])
            sub += h
            print(f"{x['term']:<14}{x['name'][:24]:<26}{num(x['credit']):>6.1f}{h:>6.0f}")
        print(f"{'':<14}{'小计':<26}{sum(num(x['credit']) for x in rs):>6.1f}{sub:>6.0f}")
        print()
        grand += sub
    print("-" * 52)
    print(f"{'合计':<14}{len(eff)} 门{'':<19}{sum(num(x['credit']) for x in eff):>6.1f}{grand:>6.0f}")


def cmd_calendar(s, term=None):
    """教学周历 —— 每周边号与日期，含节假日备注"""
    t = dec(s.get(BASE + "/jxzl/jxzl_query", timeout=25))
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, flags=re.S | re.I):
        cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
              for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        cs = [c for c in cs if c]
        if cs and (cs[0].isdigit() or cs[0] == "星期一"):
            rows.append(cs)
    if not rows:
        print("  没有周历数据")
        return
    head = rows[0]
    body = [r for r in rows[1:] if r[0].isdigit()]
    print(f"  教学周历  共 {len(body)} 周\n")
    print(f"  {'周':<4}{'周一':<8}{'周二':<8}{'周三':<8}{'周四':<8}{'周五':<8}{'周六':<12}{'周日':<12}备注")
    for r in body:
        g = lambda i: r[i] if i < len(r) else ""
        # 周六周日带月份，其余只有日
        note = r[8] if len(r) > 8 else ""
        print(f"  {g(0):<4}{g(1):<8}{g(2):<8}{g(3):<8}{g(4):<8}{g(5):<8}{g(6):<12}{g(7):<12}{note}")


def cmd_page(s, key=None):
    """通用页面抓取 —— 直接看某个菜单页的表格内容
    用法: jw.py page <关键字>   如 page 公告 / page 评价 / page 实践
    """
    PAGES = {
        "周历": "/jxzl/jxzl_query",     "桌面": "/jxzl/jxzl_query",
        "学籍": "/xsxj/xjxxgl.do",
        "实践": "/view/syjx/syyy_find.jsp",
        "评价": "/xspj/xspj_find.do",    "评教": "/xspj/xspj_find.do",
        "交流": "/jlsgl/jlxm_find.do",
        "执行计划": "/pyfa/pyfa_query",
        "方案": "/pyfa/topyfamx",
        "公告": "/ggly/ysgg_query",
        "留言": "/ggly/ysly_query",
        "进度": "/xkgl/skjhQuery.do",
        "社会考试": "/xsdjks/xsdjks_list",
        "预选": "/xkgl/xsyxgl",
        "辅修": "/fxgl/fxbmxx_query",
        "教材": "/xsjc/xsjc",
        "实验课表": "/syjx/toXskb.do",
        "班级课表": "/kbcx/kbxx_xzb",
    }
    if not key:
        print("  可用页面：")
        for k in PAGES:
            print(f"    {k}")
        return
    hit = None
    for k, v in PAGES.items():
        if key in k or k in key:
            hit = (k, v); break
    if not hit:
        print(f"  未找到页面「{key}」，可用：{' '.join(PAGES)}")
        return
    label, path = hit
    t = dec(s.get(BASE + path, timeout=25))
    print(f"  {label}  {path}  ({len(t)} 字节)\n")

    tables = re.findall(r"<table[^>]*>.*?</table>", t, flags=re.S | re.I)
    shown = 0
    for tb in tables:
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tb, flags=re.S | re.I):
            cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
                  for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
            cs = [c for c in cs if c]
            if cs: rows.append(cs)
        if len(rows) < 2: continue
        shown += 1
        head = rows[0]
        print("  " + " | ".join(head))
        print("  " + "-" * min(60, len(" | ".join(head)) * 2))
        for r in rows[1:13]:
            print("  " + " | ".join(r))
        if len(rows) > 13:
            print(f"  ... 另有 {len(rows)-13} 行")
        print()
    if not shown:
        print("  （页面无表格数据）")


# ==================== 各菜单页专用命令 ====================

def _tab(url, post=None, timeout=30):
    """取页面并解析所有表格，返回 [(head, rows)]"""
    r = s_post(url, post, timeout) if post else s_get(url, timeout)
    t = r if isinstance(r, str) else dec(r)
    out = []
    for tb in re.findall(r"<table[^>]*>.*?</table>", t, flags=re.S | re.I):
        head, rows = [], []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tb, flags=re.S | re.I):
            cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
                  for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
            cs = [c for c in cs if c]
            if not cs: continue
            if not head: head = cs
            else: rows.append(cs)
        # 过滤伪表格：表头是下拉框选项串（含「请选择」或「第1周」等）
        joined = " ".join(head)
        if "请选择" in joined or "第1周" in joined or len(joined) > 200:
            continue
        if head and rows: out.append((head, rows))
    return out, t


def _show(tabs, limit=200, title=""):
    if title: print(f"  {title}\n")
    if not tabs:
        print("  无数据")
        return
    for head, rows in tabs:
        print("  " + " | ".join(head))
        print("  " + "-" * 64)
        for r in rows[:limit]:
            print("  " + " | ".join(r))
        if len(rows) > limit:
            print(f"  ... 共 {len(rows)} 行")
        print()


def cmd_plan(s, term=None):
    """执行计划 —— 本学期开课一览（含是否考试）"""
    tabs, _ = _tab("/pyfa/pyfa_query")
    if term:
        tabs = [(h, [r for r in rows if any(term in c for c in r)]) for h, rows in tabs]
        tabs = [(h, rows) for h, rows in tabs if rows]
    _show(tabs, title="执行计划")


def cmd_teaching(s, term="2026-2027-1"):
    """教学进度 —— 授课计划"""
    tabs, _ = _tab("/xkgl/skjhxx", {"xnxqid": term})
    _show(tabs, title=f"教学进度 {term}")


def cmd_society(s):
    """社会考试报名 —— 可报名 + 已报名"""
    tabs, _ = _tab("/xsdjks/xsdjks_list")
    if not tabs:
        print("  无社会考试信息")
        return
    names = ["可报名的社会考试", "已报名的社会考试"]
    for i, (head, rows) in enumerate(tabs):
        print(f"  【{names[i] if i < len(names) else '表格'+str(i+1)}】")
        print("  " + " | ".join(head))
        for r in rows:
            print("  " + " | ".join(r))
        print()


def cmd_textbook(s, term=None):
    """教材账目"""
    tabs, _ = _tab("/xsjc/xsjc")
    _show(tabs, title="教材账目")


def cmd_eval(s):
    """教学评价 —— 待评 / 已评"""
    tabs, _ = _tab("/xspj/xspj_find.do")
    _show(tabs, title="教学评价")


def cmd_exchange(s):
    """交流申请"""
    tabs, _ = _tab("/jlsgl/jlxm_find.do")
    _show(tabs, title="交流申请")


def cmd_notice(s, kind="公告"):
    """通知公告 / 留言"""
    path = "/ggly/ysgg_query" if "公告" in kind else "/ggly/ysly_query"
    tabs, _ = _tab(path)
    _show(tabs, title=kind)


def cmd_practice(s, term=None):
    """实践环节 —— 实验预约"""
    tabs, _ = _tab("/view/syjx/syyy_find.jsp")
    _show(tabs, title="实践环节")


def cmd_labtable(s, term=None):
    """实验课表"""
    url = "/syjx/toXskb.do" + (f"?xnxq01id={term}" if term else "")
    tabs, t = _tab(url)
    if not tabs:
        print("  实验课表为空（本学期可能无实验课）")
        return
    _show(tabs, title="实验课表")


def cmd_preselect(s):
    """学生预选管理"""
    tabs, _ = _tab("/xkgl/xsyxgl")
    _show(tabs, title="预选管理")


def cmd_minor(s, term=None):
    """辅修报名"""
    tabs, _ = _tab("/fxgl/fxbmxx_query")
    _show(tabs, title="辅修报名")


def cmd_xjxx(s):
    """学籍信息管理 —— 信息修改申请记录"""
    tabs, _ = _tab("/xsxj/xjxxgl.do")
    _show(tabs, title="学籍信息修改记录")


def cmd_missing(s):
    """未获得学分/未修课程 —— 比 progress 更官方、更直接"""
    r = s.post(BASE + "/kscj/whdxfcx_list",
               data={"kksj": "", "kcxz": "", "kcmc": ""}, timeout=30)
    t = dec(r)
    recs = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, flags=re.S | re.I):
        cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
              for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        cs = [c for c in cs if c]
        if len(cs) >= 9 and cs[0].isdigit():
            recs.append({"term": cs[1], "name": cs[2], "code": cs[3],
                         "type": cs[4], "attr": cs[5], "credit": cs[6],
                         "dept": cs[7], "status": cs[8]})
    if not recs:
        print("  全部已修完")
        return
    def num(x):
        try: return float(x)
        except Exception: return 0.0
    # 按课程名去重（形势与政策等多学期课程）
    uniq = {}
    for r_ in recs:
        if r_["name"] not in uniq:
            uniq[r_["name"]] = r_
    print(f"  官方未获得学分记录 {len(recs)} 条，去重后 {len(uniq)} 门\n")
    from collections import defaultdict
    by = defaultdict(list)
    for r_ in uniq.values():
        by[r_["attr"]].append(r_)
    for attr in ("必修", "限选", "选修"):
        if attr not in by: continue
        tot = sum(num(x["credit"]) for x in by[attr])
        print(f"  【{attr}】{len(by[attr])} 门 / {tot:.1f} 学分")
        for x in sorted(by[attr], key=lambda z: (z["term"], z["name"])):
            print(f"     {x['term']:<13}{x['name'][:26]:<28}{x['credit']:>4} 学分  {x['status']}")
        print()


def cmd_exams(s, term=None):
    """考试安排 —— 走 xsks/xsksap_list"""
    data = {"xnxq01id": term or "", "xqlb": ""}
    r = s.post(BASE + "/xsks/xsksap_list", data=data, timeout=25)
    t = dec(r)
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, flags=re.S | re.I):
        cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
              for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        cs = [c for c in cs if c]
        if len(cs) >= 5 and cs[0].isdigit():
            rows.append(cs)
    if not rows:
        print("  暂无考试安排")
        return
    print(f"  {'场次':<8}{'课程':<24}{'考试时间':<20}{'考场':<10}{'座位'}")
    for r_ in rows:
        get = lambda i: r_[i] if i < len(r_) else ""
        print(f"  {get(1):<8}{get(3)[:22]:<24}{get(4):<20}{get(5):<10}{get(6)}")


def cmd_progress(s):
    """培养方案 vs 成绩单"""
    py = dec(s.get(BASE + "/pyfa/topyfamx", timeout=30))
    cell = [c.strip() for c in strip(py).split("\x01")]
    cell = [c for c in cell if c]
    plan, i = [], 0
    while i < len(cell):
        if re.fullmatch(r"\d{9}", cell[i]):
            code = cell[i]
            nm = cell[i+1] if i+1 < len(cell) else ""
            tp = cell[i+2] if i+2 < len(cell) else ""
            cr = cell[i+3] if i+3 < len(cell) else ""
            plan.append({"code": code, "name": nm, "type": tp, "credit": cr})
            i += 4
        else:
            i += 1

    r = s.post(BASE + "/kscj/cjcx_list",
               data={"kksj": "", "kcxz": "", "kcmc": "", "xsfs": "all"}, timeout=25)
    passed = set()
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", dec(r), flags=re.S | re.I):
        cs = [re.sub(r"\s+", " ", strip(x)).strip()
              for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        cs = [c for c in cs if c]
        r_ = align_grade_row(cs)
        if r_:
            try:
                if float(r_["gpa"]) > 0: passed.add(r_["name"])
            except Exception: pass

    def num(x):
        try: return float(x)
        except Exception: return 0.0
    from collections import defaultdict
    tot = defaultdict(lambda: [0, 0.0])
    miss = defaultdict(list)
    for p in plan:
        tot[p["type"]][0] += 1
        tot[p["type"]][1] += num(p["credit"])
        if p["name"] not in passed:
            miss[p["type"]].append(p)
    print(f"培养方案 {len(plan)} 门 | 已通过 {len(passed)} 门\n")
    for tp in ("必修", "限选", "选修"):
        if tp not in tot: continue
        n, c = tot[tp]
        got = sum(num(x["credit"]) for x in plan if x["type"] == tp and x["name"] in passed)
        print(f"【{tp}】{n} 门 / {c:.1f} 学分 → 已得 {got:.1f}，缺 {c-got:.1f}")
        for m in miss[tp]:
            print(f"    未通过: {m['name'][:30]:<32}{m['credit']:>5} 学分")
        print()

# ==================== 选课（实战验证版 2026-10-30）====================

XK_RID_HINT = "轮次ID从 /xsxk/xklc_list 或浏览器地址栏 jx0502zbid= 取"


def _xk_get(s, path, tries=6, need=8000, tag=""):
    """带重试的 GET，返回 (html, ok)
    高峰期 502 率约 70%，必须多试
    """
    last = ""
    for i in range(tries):
        try:
            t = dec(s.get(BASE + path, timeout=18, headers={"Referer": BASE + "/xsxk/xklc_list"}))
        except Exception:
            t = ""
        if len(t) > need and "用户登录" not in t:
            return t, True
        if t:
            last = f"{len(t)}B"
        time.sleep(1)
    return last, False


def _xk_post(s, path, data, tries=8):
    """POST 并解析 JSON。
    注意: 课程列表接口返回 {aaData:[...]}，没有 success 字段；
    提交接口返回 {success:true,...}。两种都要认。
    """
    for i in range(tries):
        try:
            r = s.post(BASE + path, data=data, timeout=30,
                       headers={"Referer": BASE + "/xsxkkc/comeInGgxxkxk"})
            t = dec(r).strip()
            if t.startswith("{") or t.startswith("["):
                try:
                    obj = json.loads(t)
                    if isinstance(obj, dict):
                        return obj, True
                except Exception:
                    pass
            if "选课成功" in t:
                return {"success": True, "message": "选课成功"}, True
        except Exception:
            pass
        time.sleep(1)
    return {}, False


def cmd_rounds(s):
    """查选课轮次
    注意: /xsxk/xklc_list 在未开放时只有「未查询到数据」，
    真正入口在 /xsxk/xklc_view?jx0502zbid=<ID>
    """
    t = dec(s.get(BASE + "/xsxk/xklc_list", timeout=20))
    if "未查询到数据" in t:
        # 尝试从会话里找已知轮次
        print("  xklc_list 无数据（可能未开放，或轮次入口不在这个页面）")
        print(f"  提示: {XK_RID_HINT}")
        return []
    rs = list(dict.fromkeys(re.findall(r"jx0502zbid=([A-Z0-9]{20,})", t)))
    rs += list(dict.fromkeys(re.findall(r"comeInXkIndx\('([^']+)'\)", t)))
    rs = list(dict.fromkeys(rs))
    if not rs:
        print("  未找到轮次ID")
        print(f"  提示: {XK_RID_HINT}")
    for r in rs:
        print(f"  轮次 id = {r}")
    return rs


def cmd_xklist(s, rid=None):
    """查可选课程

    ⚠ 重要发现（2026-10-30 实测）：
      /xsxkkc/xsxkGgxxkxk 这个课程列表接口在选课开放首小时可用（返回 JSON），
      之后就变成 404 或 {"flag1":2,"msgContent":"请先登录系统"}。
      原因不明，推测与选课批次时效或会话状态有关。

    可靠的做法：在选课开放时立刻把列表存下来，之后从存档读。
    脚本会自动读取 /workspace/xk/data/courses_*.json
    """
    import glob
    files = sorted(glob.glob(os.path.join(WD, "..", "xk", "data", "courses_*.json")))
    files += sorted(glob.glob("/workspace/xk/data/courses_*.json"))
    if files:
        f = files[-1]
        try:
            d = json.load(open(f, encoding="utf-8"))
            rows = d.get("aaData") or []
            print(f"  从存档读取: {f}（{len(rows)} 门）\n")
            print(f"  {'课程号':<12}{'课程名':<26}{'学分':>4}{'剩余':>6}")
            print("  " + "-" * 52)
            for r in rows:
                print(f"  {str(r.get('kch',''))[:11]:<12}{str(r.get('kcmc',''))[:24]:<26}"
                      f"{str(r.get('xf','')):>4}{str(r.get('syrs','')):>6}")
            print()
            print("  提交参数：")
            for r in rows:
                if str(r.get("xf")) in ("2", "1.5"):
                    print(f"    {str(r.get('kcmc',''))[:22]:<24} "
                          f"--kcid {r.get('jx02id')} --jid {r.get('jx0404id')}")
            return
        except Exception as e:
            print("  存档读取失败:", e)

    print("  无课程列表存档。")
    print("  课程列表接口只在选课开放期间可用，请在开放时运行：")
    print("    jw.py xkfetch <轮次ID>    （抓取并保存课程列表）")
    print()
    print("  或从浏览器 F12 → Network → xsxkGgxxkxk 的响应里抄 kcid / jx0404id")


def cmd_xkfetch(s, rid=None):
    """抓取课程列表并存档（仅选课开放期间可用）"""
    if not rid:
        print("  用法: jw.py xkfetch <轮次ID>")
        return
    print("  1/3 轮次页...", flush=True)
    v, ok = _xk_get(s, f"/xsxk/xklc_view?jx0502zbid={rid}", need=8000, tries=10)
    print(f"      {'OK' if ok else '失败'}", flush=True)
    if not ok: return
    print("  2/3 选课上下文...", flush=True)
    i, ok2 = _xk_get(s, f"/xsxk/xsxk_index?jx0502zbid={rid}", need=2500, tries=10)
    print(f"      {'OK' if ok2 else '失败'}", flush=True)
    if not ok2: return
    print("  3/3 选课页...", flush=True)
    g, ok3 = _xk_get(s, "/xsxkkc/comeInGgxxkxk", need=4000, tries=10)
    print(f"      {'OK' if ok3 else '失败'}", flush=True)
    if not ok3: return

    body = ("sEcho=1&iColumns=9&sColumns=&iDisplayStart=0&iDisplayLength=500"
            "&sSearch=&bRegex=false&iSortCol_0=0&sSortDir_0=asc&iSortingCols=1")
    url = ("/xsxkkc/xsxkGgxxkxk?kcxx=&skls=&skxq=&skjc="
           "&sfym=false&sfct=false&szjylb=&sfxx=false")
    print("  拉取列表...", flush=True)
    for n in range(12):
        try:
            r = s.post(BASE + url, data=body, timeout=30,
                       headers={"Referer": BASE + "/xsxkkc/comeInGgxxkxk",
                                "X-Requested-With": "XMLHttpRequest"})
            t = r.text.strip()
            if t.startswith("{"):
                d = json.loads(t)
                rows = d.get("aaData") or []
                if rows:
                    dd = os.path.join(os.path.dirname(os.path.abspath(CK)), "..", "xk", "data")
                    dd = os.path.abspath(dd)
                    os.makedirs(dd, exist_ok=True)
                    fn = os.path.join(dd, f"courses_{rid[:8]}.json")
                    json.dump(d, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
                    print(f"      ★ 成功 {len(rows)} 门 → {fn}", flush=True)
                    return
            time.sleep(1)
        except Exception:
            time.sleep(1)
    print("      列表接口不可用（仅选课开放期间可用）", flush=True)


def cmd_xkresult(s, rid=None):
    """查已选课程结果（需先建选课上下文）"""
    if not rid:
        print("  用法: jw.py xkresult <轮次ID>")
        return
    print("  1/3 轮次页...", flush=True)
    v, ok = _xk_get(s, f"/xsxk/xklc_view?jx0502zbid={rid}", need=8000)
    print(f"      {'OK' if ok else '失败'}", flush=True)
    if not ok:
        return
    print("  2/3 选课上下文...", flush=True)
    i, ok2 = _xk_get(s, f"/xsxk/xsxk_index?jx0502zbid={rid}", need=2500)
    print(f"      {'OK' if ok2 else '失败'}", flush=True)
    if not ok2:
        return
    print("  3/3 选课结果...", flush=True)
    for n in range(6):
        try:
            t = dec(s.get(BASE + "/xsxkjg/comeXkjglb", timeout=20))
        except Exception:
            t = ""
        if len(t) > 5000:
            break
        time.sleep(1)
    if len(t) <= 5000:
        print("      查询失败（服务器繁忙）")
        return
    print()
    cnt = 0
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, flags=re.S | re.I):
        cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
              for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        cs = [c for c in cs if c]
        if len(cs) >= 6 and ("选中" in cs or "退选" in cs):
            cnt += 1
            print("  " + " | ".join(cs[:8]))
    print(f"\n  共 {cnt} 门已选")


def cmd_xkgrab(s, rid=None, want=None, at=None, kcid=None, jid=None):
    """抢课（实战验证流程）

    两种用法：
      jw.py xkgrab <轮次ID> --kcid <kcid> --jid <jx0404id>   ← 推荐，直接提交
      jw.py xkgrab <轮次ID> --want "课程名"                   ← 需列表接口，可能失效
    """

    def hms(x):
        p = (x.split(":") + ["0", "0"])[:3]
        return int(p[0]), int(p[1]), int(p[2])

    if at:
        h, m, sec = hms(at)
        tgt = datetime.now().replace(hour=h, minute=m, second=sec, microsecond=0)
        print(f"  卡点 {tgt:%H:%M:%S}")
        while datetime.now() < tgt:
            time.sleep(0.2)

    if not rid:
        print("  用法: jw.py xkgrab <轮次ID> --kcid <kcid> --jid <jx0404id>")
        return

    # 直接提交模式
    if kcid and jid:
        print(f"  直接提交 kcid={kcid} jid={jid}", flush=True)
        print("  [1] 轮次页...", flush=True)
        v, ok = _xk_get(s, f"/xsxk/xklc_view?jx0502zbid={rid}", need=8000)
        print(f"      {'OK' if ok else '失败'}", flush=True)
        if not ok: return False
        print("  [2] 选课上下文...", flush=True)
        i, ok2 = _xk_get(s, f"/xsxk/xsxk_index?jx0502zbid={rid}", need=2500)
        print(f"      {'OK' if ok2 else '失败'}", flush=True)
        if not ok2: return False
        print("  [3] 选课页...", flush=True)
        g, ok3 = _xk_get(s, "/xsxkkc/comeInGgxxkxk", need=4000)
        print(f"      {'OK' if ok3 else '失败'}", flush=True)
        if not ok3: return False

        print("  [4] 提交...", flush=True)
        for n in range(12):
            try:
                r = s.post(BASE + f"/xsxkkc/ggxxkxkOper?kcid={kcid}&cfbs=",
                           data=f"jx0404id={jid}&xkzy=&trjf=", timeout=25,
                           headers={"Referer": BASE + "/xsxkkc/comeInGgxxkxk"})
                txt = r.text.strip()
                print(f"      [{n}] {r.status_code} {txt[:100]}", flush=True)
                if txt.startswith("{"):
                    res = json.loads(txt)
                    if res.get("success"):
                        print("      ★★★ 选课成功", flush=True)
                        return True
                    msg = str(res.get("message") or res.get("msgContent") or "")
                    if msg and msg != "None":
                        if "已选" in msg or "重复" in msg or "已经" in msg:
                            print("      （已选过，算成功）", flush=True)
                            return True
                        if "别处登录" in msg:
                            print("      → 会话被挤，需重登", flush=True)
                            return False
            except Exception as e:
                print(f"      [{n}] {type(e).__name__}", flush=True)
            time.sleep(1)
        return False

    names = [x.strip() for x in (want or "").split(",") if x.strip()]
    if not names:
        print("  请用 --want 指定课程名，或用 --kcid/--jid 直接提交")
        return

    if at:
        hh, mm, ss = (at.split(":") + ["0", "0"])[:3]
        tgt = datetime.now().replace(hour=int(hh), minute=int(mm),
                                     second=int(float(ss)), microsecond=0)
        print(f"  卡点 {tgt:%H:%M:%S}")
        while datetime.now() < tgt:
            time.sleep(0.2)
    print(f"  目标: {','.join(names)}", flush=True)

    # 1) 轮次页
    print("  [1] 轮次页...", flush=True)
    v, ok = _xk_get(s, f"/xsxk/xklc_view?jx0502zbid={rid}", need=8000)
    print(f"      {'OK' if ok else '失败'}", flush=True)
    if not ok:
        return
    # 2) 选课上下文
    print("  [2] 选课上下文...", flush=True)
    i, ok2 = _xk_get(s, f"/xsxk/xsxk_index?jx0502zbid={rid}", need=2500)
    print(f"      {'OK' if ok2 else '失败'}", flush=True)
    if not ok2:
        return
    # 3) 选课页
    print("  [3] 选课页...", flush=True)
    g, ok3 = _xk_get(s, "/xsxkkc/comeInGgxxkxk", need=4000)
    print(f"      {'OK' if ok3 else '失败'}", flush=True)
    if not ok3:
        return
    # 4) 拉列表找目标
    print("  [4] 拉课程列表...", flush=True)
    body = ("sEcho=1&iColumns=9&sColumns=&iDisplayStart=0&iDisplayLength=300"
            "&sSearch=&bRegex=false&iSortCol_0=0&sSortDir_0=asc&iSortingCols=1")
    url = ("/xsxkkc/xsxkGgxxkxk?kcxx=&skls=&skxq=&skjc="
           "&sfym=false&sfct=false&szjylb=&sfxx=false")
    data, ok4 = _xk_post(s, url, body)
    rows = data.get("aaData") or [] if ok4 else []
    if not rows:
        print("      列表拉取失败")
        return

    targets = []
    for r in rows:
        nm = str(r.get("kcmc", ""))
        for n in names:
            if n in nm:
                targets.append(r)
                break
    if not targets:
        print(f"      未找到目标课程，可选共 {len(rows)} 门")
        for r in rows[:10]:
            print(f"        {r.get('kcmc')}")
        return

    print(f"      找到 {len(targets)} 门目标课", flush=True)
    for r in targets:
        print(f"        {r.get('kcmc')}  kcid={r.get('jx02id')} jid={r.get('jx0404id')} "
              f"剩余={r.get('syrs')}", flush=True)

    # 5) 提交（按优先级）
    for r in targets:
        kcid = r.get("jx02id")
        jid = r.get("jx0404id")
        print(f"  [5] 提交 {r.get('kcmc')}...", flush=True)
        res, ok5 = _xk_post(s, f"/xsxkkc/ggxxkxkOper?kcid={kcid}&cfbs=",
                            f"jx0404id={jid}&xkzy=&trjf=")
        msg = res.get("message") or res.get("msg") or str(res)
        if res.get("success"):
            print(f"      ★★★ 选课成功: {r.get('kcmc')}", flush=True)
            return True
        print(f"      {msg}", flush=True)
        if "已选" in str(msg) or "重复" in str(msg):
            print("      （可能已选过）", flush=True)
            return True
    print("  全部失败")
    return False


def cmd_grab(s, at=None, want=None, credit=None):
    """兼容旧命令名：grab 现在等价于 xkgrab，需要轮次ID"""
    rid = getattr(cmd_grab, "_rid", None)
    if not rid:
        print("  grab 需要轮次ID。请用: jw.py xkgrab <轮次ID> --want 课程名")
        print(f"  {XK_RID_HINT}")
        return False
    return cmd_xkgrab(s, rid, want, at, getattr(cmd_grab,"_kcid",None), getattr(cmd_grab,"_jid",None))


# ---------------- 入口 ----------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["login", "alive", "keepalive", "info", "grades",
                                    "timetable", "exams", "progress", "missing", "hours", "calendar", "page", "plan", "teaching", "society", "textbook",
                                    "eval", "exchange", "notice", "practice", "labtable",
                                    "preselect", "minor", "xjxx", "rounds", "grab", "doctor",
                                    "xklist", "xkfetch", "xkresult", "xkgrab"])
    ap.add_argument("arg", nargs="?", default=None, help="学期参数")
    ap.add_argument("-u", "--user"); ap.add_argument("-p", "--pwd")
    ap.add_argument("--gpa", action="store_true")
    ap.add_argument("--term")
    ap.add_argument("--at"); ap.add_argument("--want"); ap.add_argument("--credit", type=float)
    ap.add_argument("--raw", action="store_true", help="显示身份证等敏感字段的完整值")
    ap.add_argument("--from", dest="frm", default="11:58", help="开始轮询时间，默认 11:58")
    ap.add_argument("--speed", type=float, default=3.0, help="低频轮询间隔秒，默认 3")
    ap.add_argument("--boost", type=float, default=0.2, help="发现轮次后的间隔秒，默认 0.2")
    ap.add_argument("--kcid", default=None, help="课程 kcid（=jx02id），直接提交用")
    ap.add_argument("--jid", default=None, help="jx0404id，直接提交用")
    a = ap.parse_args()
    globals()["RAW"] = a.raw
    if a.arg:
        cmd_grab._rid = a.arg

    s = newsess()
    globals()["G_S"] = s

    if a.cmd == "doctor":
        sys.exit(cmd_doctor())

    if a.cmd == "login":
        ok = do_login(s, a.user, a.pwd)
        print("✅ 登录成功，session 已保存" if ok else "❌ 登录失败：学号或密码不正确")
        sys.exit(0 if ok else 1)
    if a.cmd == "alive":
        load_ck(s); ok = logged_in(s)
        print("✅ session 有效" if ok else "❌ 已失效")
        sys.exit(0 if ok else 1)
    if a.cmd == "keepalive":
        load_ck(s); print("OK" if keepalive(s) else "FAIL")
        sys.exit(0)

    if not ensure(s):
        print()
        print("❌ 无法建立会话。")
        print("   首次使用请先运行：  jw.py login")
        print("   然后按其提示输入学号与密码。")
        sys.exit(1)

    term = a.term or a.arg
    {"info": lambda: cmd_info(s),
     "grades": lambda: cmd_grades(s, a.gpa, term),
     "timetable": lambda: cmd_timetable(s, term),
     "exams": lambda: cmd_exams(s, term),
     "progress": lambda: cmd_progress(s),
     "missing": lambda: cmd_missing(s),
     "hours": lambda: cmd_hours(s, term),
     "calendar": lambda: cmd_calendar(s, term),
     "page": lambda: cmd_page(s, term),
     "plan": lambda: cmd_plan(s, term),
     "teaching": lambda: cmd_teaching(s, term or "2026-2027-1"),
     "society": lambda: cmd_society(s),
     "textbook": lambda: cmd_textbook(s, term),
     "eval": lambda: cmd_eval(s),
     "exchange": lambda: cmd_exchange(s),
     "notice": lambda: cmd_notice(s, term or "公告"),
     "practice": lambda: cmd_practice(s, term),
     "labtable": lambda: cmd_labtable(s, term),
     "preselect": lambda: cmd_preselect(s),
     "minor": lambda: cmd_minor(s, term),
     "xjxx": lambda: cmd_xjxx(s),
     "rounds": lambda: cmd_rounds(s),
     "grab": lambda: cmd_grab(s, a.at, a.want, a.credit),
     "xklist": lambda: cmd_xklist(s, a.term or a.arg),
     "xkfetch": lambda: cmd_xkfetch(s, a.term or a.arg),
     "xkresult": lambda: cmd_xkresult(s, a.term or a.arg),
     "xkgrab": lambda: cmd_xkgrab(s, a.term or a.arg, a.want, a.at, a.kcid, a.jid)}[a.cmd]()
