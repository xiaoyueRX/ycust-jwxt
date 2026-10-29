# 接口详解

BASE = `http://jw.ycust.com/yckjxy_jsxsd`

## 0. 系统特征

- 后端：湖南强智科技 jsxsd（很多高校通用，思路可迁移）
- 前端：老式 frameset + jQuery 1.x，页面内 `setInterval` 每 10 分钟刷新 `blankPage.jsp` 保活
- 编码：**混杂**。首页是 GBK，`xsMain.jsp` 等是 UTF-8。必须逐个尝试解码
- 链接普遍带 `?Ves632DSdyV=NEW_XSD_XXX` 参数，**实测非必需**，去掉也能访问（页面略小，因为少了对应模块的注入）

## 1. 登录

```
GET  /                        → 拿 JSESSIONID
POST /xk/LoginToXk
     encoded=<b64(学号)>%%%<b64(密码)>
     jzmmid=1
     Referer: BASE/
→ 302 Location: /framework/xsMain.jsp   成功
→ 200 停留登录页                          失败
```

`encoded` 的 `%%%` 是三个百分号，字面量。前端 `encodeInp()` 就是标准 base64（`conwork.js` 里是打包过的实现）。

登录态检测：`GET /framework/xsMain.jsp`，返回体含 `学生个人中心` 即有效。

保活：`GET /framework/blankPage.jsp` 返回 6 字节，最轻量。

## 2. 学籍卡片

```
GET /grxx/xsxx
```

页面里字段是 `字段名：值` 的扁平文本，用正则 `院系[：:]\s*(\S+)` 之类提取。可得：院系、专业、学制、班级、学号、姓名、性别、出生日期、政治面貌、民族、学习层次。

注意 `学籍变动情况` 表格里有历史分班记录（如工程师班重新分班）。

## 3. 成绩

```
POST /kscj/cjcx_list
     kksj=<学年学期，空=全部>
     kcxz=
     kcmc=
     xsfs=all
```

返回 HTML 表格，列为：
`序号 | 开课学期 | 课程编号 | 课程名称 | 成绩 | 成绩标志 | 学分 | 总学时 | 绩点 | 考核方式 | 课程属性 | 课程性质`

**关键坑：重修会留下多条记录。** 例如高等数学Ⅱ可能出现 35、45、60 三行，绩点 0/0/1。必须按 `(学期, 课程编号)` 去重，取绩点最大的一条，否则学分重复累加、绩点被拉低。

成绩可能是百分制数字，也可能是五级制文字（优秀/良好/中等/及格/不及格）。换算（见学校 92 号文）：
优秀=95, 良好=85, 中等=75, 及格=65, 不及格=0

## 4. 课表

```
GET /xskb/xskb_list.do?xnxq01id=2026-2027-1&zc=<周次>
```

结构（重要）：

```html
<td>
  <input type="hidden" name="jx0415zbdiv_2" value="XXX-1-2">
  <div id="XXX-1-1" class="kbcontent1">      <!-- 首页显示的那个 -->
    课程名<br/>
    <font title='周次(节次)'>班级+周次</font><br/>
    <font title='教室'>A8-505</font><br/>
  </div>
  <div id="XXX-1-2" style="display:none" class="kbcontent">  <!-- 隐藏的详情 -->
    课程名[48][限选]<br/>
    <font title='老师'>姓名</font><br/>
    <font title='班级'>...</font><br/>
  </div>
</td>
```

一个 `td` 里可能有**多门课**（不同周次），用 `------` 分隔。

**关键：必须解析成二维表。** 完整表格是：

```html
<table id="kbtable">
  <tr><th></th><th>星期一</th><th>星期二</th>...<th>星期日</th></tr>
  <tr>
    <th>第1.2节</th>
    <td> ...课程格子... </td>
    <td> ... </td>
  </tr>
  <tr><th>第3.4节</th>...</tr>
</table>
```

- 首行 `th` 是星期一到星期日
- 每行第一个 `th` 是节次（`第1.2节`）
- 其余 `td` 按列对应星期

如果只抓 `kbcontent1` 而不记录它所在的 `tr`/`td` 位置，就会丢掉「周几第几节」，输出的课表毫无用途。

正则要写 `class="kbcontent1"`（精确），写 `kbcontent` 会漏匹配。

## 5. 培养方案

```
GET /pyfa/topyfamx
```

页面是「课程设置总表」。课程行的特征是**课号是 9 位数字**，紧跟着依次是：课程名称、类别（必修/限选/选修）、学分、若干学时列。

用状态机扫：遇到 9 位数字就吃掉后面 4 个单元格。

比对成绩单时**按课程名称匹配**，因为培养方案和成绩单的课号体系可能不同（成绩单里公选课用 `mk*`、`xtzx*` 前缀，培养方案里没有）。

学分口径随专业和年级不同差异很大。实测某工科专业某一届的三个模块大致量级：
- 必修 70~80 门 / 130~140 学分
- 限选约 20 门 / 35~40 学分
- 选修约 15 门 / 25~30 学分

**不要套用别人的数字**，必须以本账号 `topyfamx` 返回的实际表格为准。

## 6. 选课

### 轮次列表

```
GET /xsxk/xklc_list
```

轮次行的操作列是 `onclick="comeInXkIndx('***')"`。用正则 `comeInXkIndx\('([^']+)'\)` 提取 id。

**未开放时返回 `未查询到数据`，此时所有 `xsxk/*` 接口一律返回「非法访问」页（772 字节）。这是正常的，不是权限问题。**

### 选课页

```
GET /xsxk/xsxk_index?jx0502zbid=<轮次id>
GET /xsxk/xsxk_index.jsp?jx0502zbid=<轮次id>     （备用）
```

### 已知会被拦的路径

以下路径在无轮次时返回「非法访问」：`xsxk/xsxk_list`、`xsxk/xkOper`、`xsxk/xsxk_index`。

### 抢课策略

1. **提前登录**，复用 session（见 SKILL.md 主文档）
2. 12:00 前 3 秒开始轮询 `xklc_list`
3. 出现轮次立刻抓选课页，**先把 HTML 存盘**，因为开放瞬间的结构无法提前预知
4. 从课程行的 `href` / `onclick` 里找提交入口
5. 命中即停——**选课一经提交不可退改**

## 7. 考试安排

```
GET /xsks/xsksap_query
```

带 `学年学期` 和 `学期类别`（期初/期中/期末）下拉。未排考时表格为空。

## 8. 其他可用入口（从 xsMain.jsp 提取）

| 页面 | 路径 |
|---|---|
| 我的桌面 | `framework/xsMain.jsp` |
| 学籍信息管理 | `xsxj/xjxxgl.do` |
| 等级考试成绩 | `kscj/cjcx_whdxfcx` |
| 成绩认定 | `kscj/cjcx_djkscx` |
| 计划内未获得学分 | `kscj/cjcx_query` 页内链接 |
| 我的课表（实验） | `syjx/toXskb.do` |
| 班级课表 | `kbcx/kbxx_xzb` |
| 教师课表 | `kbcx/kbxx_teacher` |
| 教室课表 | `kbcx/kbxx_classroom` |
| 课程课表 | `kbcx/kbxx_kc` |
| 教材账目 | `xsjc/xsjc` |
| 辅修报名 | `fxgl/fxbmxx_query` |
| 社会考试报名 | `xsdjks/xsdjks_list` |
| 学生预选 | `xkgl/xsyxgl` |
| 重修报名选课 | `xsks/xsksap_query` 同级菜单内 |

## 8.5 社会考试报名

```
GET /xsdjks/xsdjks_list
```

页面含两张表：
1. **可报名的社会考试**：序号 / 考级课程名称 / 考级等级名称 / 考试时间 / 报名金额 / 操作
2. **已报名课程**：考级等级 / 考级课程 / 考级时间 / 金额 / 考级准考证号 / 原准考证号 / 报名验证码 / 是否缴费 / 缴费状态原因

操作列走 `funBm(url, msg)` → `openWindow(url, 760, 550)` 弹窗报名。当学期没有社会考试时两张表都是「未查询到数据」，属正常。

**注意**：这个页面和「等级考试成绩」`kscj/cjcx_djkscx` 不是一回事——后者始终返回「非法访问」（772B），可能不对学生开放。

## 9. 编码处理

```python
def dec(resp):
    for e in ("utf-8", "gbk", "gb18030"):
        try: return resp.content.decode(e)
        except Exception: pass
    return resp.content.decode("utf-8", "ignore")
```

不要用 `resp.text`，requests 会按错误编码猜。

---

# 附：完整接口清单（逐页扒取）

以下是遍历 `xsMain.jsp` 全部页面、提取内容区表单 action 和 inline JS 里的 URL 得到的**全部可用接口**。

## A. 数据查询类（POST 返回 HTML 表格，首选）

| 用途 | 方法 | 路径 | 参数 | 返回 |
|---|---|---|---|---|
| **未获得学分/未修课程** | POST | `kscj/whdxfcx_list` | `kksj=&kcxz=&kcmc=` | 逐门列出未修课程，含学期/课号/模块/性质/学分/单位/**状态(未修/未通过)** |
| 成绩列表 | POST | `kscj/cjcx_list` | `kksj=&kcxz=&kcmc=&xsfs=all` | 全部成绩 |
| 考试安排列表 | POST | `xsks/xsksap_list` | `xnxq01id=&xqlb=` | 场次/课程/时间/考场/座位/准考证 |
| 教学进度明细 | POST | `xkgl/skjhxx` | `xnxqid=2026-2027-1` | 授课计划，**用 xnxqid 不是 xnxq01id** |
| 实践环节查询 | POST | `syjx/findSyYyKc.do` | 空即可 | 实验预约管理 |
| 预选选课 | POST | `xkgl/xsyxxk` | — | 预选管理 |
| 辅修报名列表 | POST | `fxgl/fxbmxx` | — | 辅修信息 |

### `whdxfcx_list` 是隐藏的好东西

它比 `cjcx_list` 更直接地告诉你「还差什么」：把培养方案里**所有**课程列出来，标注哪些没修。而且**包含未来学期**（如毕业实习在 2027-2028-1），能提前规划。

列结构：
`序号 | 开课学期 | 课程名称 | 课程代码 | 课程性质 | 课程属性 | 学分 | 开课单位 | 状态`

注意同一门课会在多个学期重复出现（如「形势与政策」从大一到毕业共 8 条），这是正常的——它按**每学期都要修**来列。

## B. 导出/打印类

| 用途 | 路径 |
|---|---|
| 学籍卡片打印 | `grxx/xsxx_print.do` |
| 教学周历打印 | `jxzl/jxzl_print.do` |
| 成绩单打印 | `kscj/facjdy_list` |
| 课表打印 | `xskb/xskb_print.do` |
| 预选列表加载 | `xkgl/loadYxxkList` |

## C. 表单页（GET 展示，需 JS 触发子接口）

| 页面 | 路径 | 子接口 |
|---|---|---|
| 学籍信息管理 | `xsxj/xjxxgl.do` | `xsxj/toEditxsxx.do` |
| 课程成绩查询 | `kscj/cjcx_query` | `kscj/cjcx_list`, `kscj/facjdy_list` |
| 未获学分查询 | `kscj/cjcx_whdxfcx` | `kscj/whdxfcx_list` |
| 考试安排查询 | `xsks/xsksap_query` | `xsks/xsksap_list` |
| 学生选课中心 | `xsxk/xklc_list` | `xsxk/xsxk_index` |
| 学生预选管理 | `xkgl/xsyxgl` | `xkgl/xsyxxk` |
| 辅修报名 | `fxgl/fxbmxx_query` | `fxgl/fxbmxx` |
| 社会考试报名 | `xsdjks/xsdjks_list` | 操作走 `funBm()` 弹窗，非独立接口 |
| 教学进度查询 | `xkgl/skjhQuery.do` | `xkgl/skjhxx` |
| 实践环节 | `view/syjx/syyy_find.jsp` | `syjx/findSyYyKc.do` |
| 班级课表 | `kbcx/kbxx_xzb` | `kbcx/kbxx_xzb_ifr` |
| 教师课表 | `kbcx/kbxx_teacher` | `kbcx/kbxx_teacher_ifr` |
| 教室课表 | `kbcx/kbxx_classroom` | `kbcx/kbxx_classroom_ifr` |
| 课程课表 | `kbcx/kbxx_kc` | `kbcx/kbxx_kc_ifr` |

## D. 纯展示页（数据直接嵌在 HTML 里，无需额外请求）

`pyfa/topyfamx`（培养方案）、`pyfa/pyfajc_query`（教学进程）、`pyfa/pyfa_query`（执行计划）、`xsks/xsksap_query`、`syjx/toXskb.do`（实验课表）、`xspj/xspj_find.do`（教学评价）、`jlsgl/jlxm_find.do`（交流申请）、`ggly/ysgg_query`（公告）、`ggly/ysly_query`（留言）、`xsjc/xsjc`（教材账目）、`jxzl/jxzl_query`（周历）

## E. 确认失效的路径

- `kscj/cjcx_djkscx` → 772B「非法访问」（等级考试成绩）
- `kscj/djkscx_query`、`kscj/cjcx_djkscx_query` → 同样 772B
- 三个变体都试过，判断为**该功能不对学生账号开放**，不是参数问题

## F. 探测方法（可复现）

1. GET `framework/xsMain.jsp`，提取所有 `<a href>` 得到顶层 21 个页面
2. 每个页面 GET 一次，**从 `Nsb_layout_r` 之后开始**找 `<form action>` 和 `onclick` 里的 URL（前面是全站共用侧边栏，会污染结果）
3. 再扫 inline `<script>` 里的 `url:` / `action:` / `.load(` 字符串
4. 对候选接口 POST 空参数试一次，返回 >500B 且不含「非法访问」即有效
