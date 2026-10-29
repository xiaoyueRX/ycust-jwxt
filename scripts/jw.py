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
import requests

BASE  = "http://jw.ycust.com/yckjxy_jsxsd"
# 会话文件位置：优先用工作区，取不到就退回脚本所在目录
def _workdir():
    for d in (os.environ.get("WORKSPACE_DIR"), "/workspace", os.path.expanduser("~")):
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return os.path.dirname(os.path.abspath(__file__))

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

def logged_in(s):
    try:
        r = s.get(BASE + "/framework/xsMain.jsp", timeout=12)
        return "学生个人中心" in dec(r)
    except Exception:
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


def do_login(s, user=None, pwd=None):
    user, pwd = ask_credentials(user, pwd)
    if not user or not pwd:
        print("❌ 未提供凭据，无法登录")
        return False
    s.get(BASE + "/", timeout=15)
    enc = (base64.b64encode(user.encode()).decode() + "%%%" +
           base64.b64encode(pwd.encode()).decode())
    r = s.post(BASE + "/xk/LoginToXk",
               data={"encoded": enc, "jzmmid": "1"},
               headers={"Referer": BASE + "/"}, allow_redirects=True, timeout=20)
    ok = ("学生个人中心" in dec(r)) or ("xsMain" in r.url)
    if ok: save_ck(s)
    return ok

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


def cmd_info(s):
    t = dec(s.get(BASE + "/grxx/xsxx", timeout=20))
    flat = re.sub(r"\s+", " ", strip(t))
    for kw in ("院系", "专业", "班级", "学号", "姓名", "性别", "出生日期", "培养层次"):
        m = re.search(kw + r"[：:]\s*([^\x01]{1,30})", flat)
        if m: print(f"  {kw:6}: {m.group(1).strip()}")
    m = re.search(r"学\s*籍\s*卡\s*片", flat)

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
    """课表单元格: class="kbcontent1" 是显示内容, class="kbcontent" 是隐藏详情"""
    url = BASE + "/xskb/xskb_list.do"
    if term:
        url += "?xnxq01id=" + term
    t = dec(s.get(url, timeout=25))

    pat = re.compile(r'<div[^>]*class="kbcontent1"[^>]*>(.*?)</div>', re.S | re.I)
    fontpat = re.compile(r"<font[^>]*title='([^']+)'[^>]*>(.*?)</font>", re.S | re.I)
    fontpat2 = re.compile(r'<font[^>]*title="([^"]+)"[^>]*>(.*?)</font>', re.S | re.I)

    seen, out = set(), []
    for b in pat.findall(t):
        for fp in (fontpat, fontpat2):
            for m in fp.finditer(b):
                b = b.replace(m.group(0), m.group(1) + ": " + m.group(2))
        b = re.sub(r"<br\s*/?>", "\n", b, flags=re.I)
        lines = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
                 for x in b.split("\n")]
        lines = [x for x in lines if x and x != "\xa0"]
        if not lines:
            continue
        key = "|".join(lines)
        if key in seen:
            continue
        seen.add(key)
        out.append(lines)

    if not out:
        print("  课表为空，检查学期参数格式（如 2026-2027-1）")
        return
    print("  共 %d 个课程时段\n" % len(out))
    for i, lines in enumerate(out, 1):
        print("  [%d] %s" % (i, lines[0]))
        for x in lines[1:]:
            print("      " + x)
        print()


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

def cmd_rounds(s):
    t = dec(s.get(BASE + "/xsxk/xklc_list", timeout=20))
    rs = list(dict.fromkeys(re.findall(r"comeInXkIndx\('([^']+)'\)", t)))
    if not rs:
        print("无选课轮次（未开放）")
        if "未查询到数据" in t: print("  页面提示：未查询到数据")
    else:
        for r in rs: print(f"  轮次 id = {r}")
    return rs

def cmd_grab(s, at=None, want=None, credit=None):
    if at:
        hh, mm, ss = (at.split(":") + ["0", "0"])[:3]
        tgt = datetime.now().replace(hour=int(hh), minute=int(mm),
                                     second=int(float(ss)), microsecond=0)
        print(f"卡点 {tgt:%H:%M:%S}")
        while datetime.now() < tgt:
            time.sleep(0.05)
    names = [x.strip() for x in (want or "").split(",") if x.strip()]
    n = 0
    while True:
        n += 1
        rs = []
        try:
            t = dec(s.get(BASE + "/xsxk/xklc_list", timeout=12))
            rs = list(dict.fromkeys(re.findall(r"comeInXkIndx\('([^']+)'\)", t)))
        except Exception:
            pass
        for rid in rs:
            for u in (f"{BASE}/xsxk/xsxk_index?jx0502zbid={rid}",
                      f"{BASE}/xsxk/xsxk_index.jsp?jx0502zbid={rid}"):
                try:
                    r = s.get(u, timeout=12, headers={"Referer": BASE + "/xsxk/xklc_list"})
                    page = dec(r)
                    if len(page) < 800 or "非法访问" in page: continue
                    open(os.path.join(WD, ".jw_last_page.html"), "w", encoding="utf-8").write(page)
                    print(f"  轮次{rid} 页面已存档，{len(page)} 字节")
                    for nm in names:
                        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
                            if nm in tr:
                                for a in re.findall(r"""href=["']([^"']+)["']""", tr, re.I):
                                    if a.startswith("javascript:") or a == "#": continue
                                    url = a if a.startswith("http") else BASE + "/" + a.lstrip("/")
                                    rr = s.get(url, timeout=10, headers={"Referer": u})
                                    body = dec(rr)
                                    if any(k in body for k in ("成功", "已选")):
                                        print(f"  ★ 抢到: {nm}")
                                        return True
                except Exception:
                    pass
        if n % 20 == 0: print(f"  等待轮次... (第{n}次)")
        time.sleep(0.35)

# ---------------- 入口 ----------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["login", "alive", "keepalive", "info", "grades",
                                    "timetable", "exams", "progress", "missing", "rounds", "grab", "doctor"])
    ap.add_argument("arg", nargs="?", default=None, help="学期参数")
    ap.add_argument("-u", "--user"); ap.add_argument("-p", "--pwd")
    ap.add_argument("--gpa", action="store_true")
    ap.add_argument("--term")
    ap.add_argument("--at"); ap.add_argument("--want"); ap.add_argument("--credit", type=float)
    a = ap.parse_args()

    s = newsess()

    if a.cmd == "doctor":
        sys.exit(cmd_doctor())

    if a.cmd == "login":
        ok = do_login(s, a.user, a.pwd)
        print("✅ 登录成功，session 已保存" if ok else "❌ 登录失败")
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
     "rounds": lambda: cmd_rounds(s),
     "grab": lambda: cmd_grab(s, a.at, a.want, a.credit)}[a.cmd]()
