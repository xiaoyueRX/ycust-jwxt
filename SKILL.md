---
name: ycust-jwxt
description: 银川科技学院强智科技教务系统（jw.ycust.com/yckjxy_jsxsd）的接口手册与操作脚本。用于查询成绩、绩点、课表、考试安排、学籍信息、培养方案、学分完成度，以及选课抢课。当用户提到 教务系统、教务、查成绩、绩点、课表、抢课、选课、公选课、培养方案、学分、挂科、重修、银川科技学院 等相关话题时使用。
compatibility: 需要 python3 与 requests（pip install requests）。在工作区内运行。
---

# 银川科技学院教务系统（强智 jsxsd）

覆盖 `http://jw.ycust.com/yckjxy_jsxsd/` 的完整接口。**所有路径都相对于这个 base**。

## 装不上/用不了？先跑自检

```bash
python3 /skills/ycust-jwxt/scripts/jw.py doctor
```

会检查 python、requests、工作目录权限、网络连通性、文件完整性，以及最常见的**目录嵌套**错误（`/skills/ycust-jwxt/ycust-jwxt/`）。报错时先看这个输出。

## 首次使用：必须先向用户索取账号密码

**这个 skill 不含任何预置凭据。** 第一次使用时（`/workspace/.jw_session.txt` 不存在或已失效），必须先向用户询问：

> 请提供你的学号与密码

拿到后再执行 `python3 /skills/ycust-jwxt/scripts/jw.py login`，脚本会交互式读取，或用 `-u 学号 -p 密码` 直接传入。

**不要**把密码写进任何文件、环境变量或记忆。session 存在 `/workspace/.jw_session.txt` 后，后续调用会自动复用，不再需要密码。

如果用户拒绝提供密码，只能提示他手动登录后自行截图/复制内容给你，不要尝试绕过。

**工作目录是自适应的**：脚本优先用 `/workspace`，不可写时退回 `$HOME`，也可用环境变量 `WORKSPACE_DIR` 指定。不依赖固定路径。

## 登录方式特殊

用 base64 编码的 `encoded` 参数登录，**不是普通表单**：

```
encoded = base64(学号) + "%%%" + base64(密码)
POST /xk/LoginToXk   body: encoded=...&jzmmid=1
成功标志: 302 跳转到 /framework/xsMain.jsp
```

用 `scripts/jw.py login` 完成，不要手搓。

## 最重要的一条经验：永远先复用 session

12:00 抢课时全校同时涌入，登录接口（要查库验证密码）最先被压垮，浏览器会直接卡死。正确做法：

1. 提前（如 11:40）登录一次，把 `JSESSIONID` 存下来
2. 之后所有请求复用这个 cookie，**完全不碰登录接口**
3. 用 6 字节的 `framework/blankPage.jsp` 保活

`scripts/jw.py` 自动复用 `/workspace/.jw_session.txt`，只在失效时才重新登录。

浏览器抢课的劣势：一次完整选课要发 30+ 请求（登录+框架+菜单+图片+JS+列表），脚本只要 1~2 个。卡死的是浏览器，不是服务器。

## 接口速查表

| 用途 | 方法 | 路径 | 关键参数 |
|---|---|---|---|
| 登录 | POST | `xk/LoginToXk` | `encoded`, `jzmmid=1` |
| 登录态检测 | GET | `framework/xsMain.jsp` | 含"学生个人中心"即有效 |
| 保活 | GET | `framework/blankPage.jsp` | 无（仅 6 字节） |
| 学籍卡片 | GET | `grxx/xsxx` | — |
| 成绩查询页 | GET | `kscj/cjcx_query` | — |
| 成绩列表 | POST | `kscj/cjcx_list` | `kksj=&kcxz=&kcmc=&xsfs=all` |
| 课表 | GET | `xskb/xskb_list.do` | `xnxq01id=2026-2027-1`, `zc=周次` |
| 培养方案 | GET | `pyfa/topyfamx` | — |
| 执行计划 | GET | `pyfa/pyfa_query` | — |
| 选课中心 | GET | `xsxk/xklc_list` | — |
| 选课页 | GET | `xsxk/xsxk_index?jx0502zbid=<id>` | 轮次 id |
| 教学进度 | GET | `xkgl/skjhQuery.do` | — |
| 教学进度明细 | POST | `xkgl/skjhxx` | `xnxqid=2026-2027-1` |
| 考试安排 | GET | `xsks/xsksap_query` | 表单页 |
| 考试安排列表 | POST | `xsks/xsksap_list` | `xnxq01id=&xqlb=` |
| **未获得学分** | POST | `kscj/whdxfcx_list` | `kksj=&kcxz=&kcmc=` |
| 实践环节 | POST | `syjx/findSyYyKc.do` | 空 |
| 预选选课 | POST | `xkgl/xsyxxk` | — |
| 辅修报名 | POST | `fxgl/fxbmxx` | — |

完整字段、响应结构和坑，见 [接口详解](references/api.md)。

## 四件常用事

### 1. 查成绩 / 算绩点

```bash
python3 /skills/ycust-jwxt/scripts/jw.py grades --gpa
```

**必须去重**：同一门课重修会有多条记录（如高等数学Ⅱ：35→45→60）。取绩点最大的那条，否则学分和绩点都会算错。

### 2. 查课表

```bash
python3 /skills/ycust-jwxt/scripts/jw.py timetable 2026-2027-1
```

课表在 `td.kbcontent` 里，用 `---------------------` 分隔多个时段。

### 3. 查还差多少学分

两个命令，用途不同：

```bash
python3 /skills/ycust-jwxt/scripts/jw.py missing    # 官方口径，首选
python3 /skills/ycust-jwxt/scripts/jw.py progress   # 自己比对培养方案
```

**`missing` 更好**：直接调 `kscj/whdxfcx_list`，是教务系统自己算的「未获得学分」清单，包含未来学期（如 2027-2028 的毕业实习），能提前规划。

`progress` 是把 `pyfa/topyfamx` 与成绩单按**课程名称**比对（两边课号体系不同，只能按名称）。它作为交叉验证用。

注意 `missing` 返回里同一门课会出现多次（「形势与政策」从大一到毕业共 8 条），按课程名去重后再看。

### 4. 抢课

```bash
python3 /skills/ycust-jwxt/scripts/jw.py grab --at 12:00:00 --want "课程A,课程B"
```

按 `--want` 顺序依次提交，命中即停。**选课一经提交不可退改**，优先级必须由用户确认。

凑学分（门数最少）用 `--credit 2.0`，脚本按学分档位贪心组合。

## 抢课的坑

- **别在 12:00 登录**，见上文
- `xsxk/*` 会校验轮次，**未开放时一律返回"非法访问"**，不是 bug
- 轮次 id 从 `xsxk/xklc_list` 用 `comeInXkIndx('xxx')` 提取
- 开放瞬间页面结构未知，先存档 HTML 再决定提交格式
- 提交成功立刻停

## 排查

| 现象 | 原因 |
|---|---|
| 返回"非法访问" | 轮次未开放；或 session 失效 |
| 页面是登录页 | cookie 过期，重新 login |
| 成绩全是 0 绩点 | 那是重修失败旧记录，按最高分去重 |
| 课表空白 | `xnxq01id` 格式错，应为 `2026-2027-1` |

页面编码混杂（`utf-8` / `gbk`），`jw.py` 已处理；自己写代码要逐个尝试解码。
