# ycust-jwxt

银川科技学院（YCUST）强智科技教务系统的 Agent Skill，提供接口手册与命令行工具。

## 功能

- 成绩与加权绩点
- 课表、考试安排
- 学籍与培养方案
- 学分缺口（官方「未获得学分」口径）
- 选课与抢课

## 安装

```bash
pip install requests
```

将本目录放入 Agent 的 skills 目录，通常为 `/skills/ycust-jwxt/`，然后在扩展面板中启用。

## 使用

首次运行需要提供学号与密码：

```bash
python3 scripts/jw.py login
```

```
==============================================
  银川科技学院教务系统 — 需要登录
==============================================
  请提供你的学号与密码（仅本次会话使用，不写入磁盘）

  学号: 2024xxxxxx
  密码:
```

登录后 session 保存在 `/workspace/.jw_session.txt`，后续命令自动复用，不再需要密码。

```bash
python3 scripts/jw.py grades --gpa            # 成绩 + 加权绩点
python3 scripts/jw.py timetable 2026-2027-1   # 课表
python3 scripts/jw.py missing                 # 还差多少学分
python3 scripts/jw.py grab --at 12:00:00 --want "课程A,课程B"
```

完整命令见 `python3 scripts/jw.py --help`。

## 说明

抢课前建议先运行 `login` 提前建立 session。12:00 选课高峰时登录接口压力最大，复用 session 可以跳过这一步。

接口清单与字段说明见 [references/api.md](references/api.md)。

## 免责声明

仅供查询本人教务数据使用。请勿用于批量爬取或代他人查询。

## License

MIT
