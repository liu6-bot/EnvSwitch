# 贡献指南

感谢关注 EnvSwitch！这个项目刻意保持**零第三方依赖**，代码量不大，上手门槛很低。

## 开发环境

```bash
git clone https://github.com/<你的用户名>/EnvSwitch.git
cd EnvSwitch
python main.py            # 直接跑
python build_exe.py --check   # 检查打包环境
```

只需 Python 3.9+ 与 tkinter，无任何 pip 依赖。

## 代码结构

```
envswitch/
├── core/            # 全部业务逻辑，不依赖任何 GUI
│   ├── models.py    #   ToolDef（工具定义）/ Install（安装实例）
│   ├── builtins.py  #   内置工具定义 ← 想支持新语言，主要改这里
│   ├── config.py    #   ~/.envswitch/config.json 读写与 v1 迁移
│   ├── scanner.py   #   通用安装扫描 + 并发版本探测
│   ├── env.py       #   Windows 注册表 / Unix shell 标记块 写入后端
│   └── manager.py   #   EnvManager 门面（GUI/CLI 唯一入口）
├── ui/              # tkinter 界面
│   ├── app.py       #   主窗口
│   ├── dialogs.py   #   自定义工具编辑器 / 设置 / 关于
│   └── theme.py     #   配色与字体
└── cli.py           # 命令行入口
```

## 常见贡献方向

### 1. 添加一个内置工具

在 `envswitch/core/builtins.py` 里照抄一条 `ToolDef(...)` 加进 `BUILTIN_TOOLS` 即可，
不需要改任何逻辑代码。请在 PR 里附上你机器上 `python -m envswitch list <id>` 的输出。

### 2. 补充扫描路径

某发行版/安装器把工具装在了奇怪的位置？在对应工具的 `search_roots` 里补一行。

### 3. 平台 bug

Windows 注册表、macOS/Homebrew、Linux 各发行版的差异问题尤其欢迎，请附上
`python -m envswitch doctor` 的输出。

## 提交规范

- 一个 PR 聚焦一件事
- 提交信息用祈使句：`Add ruby builtin tool definition`
- 涉及行为变化请同步更新 `README.md` 与 `CHANGELOG.md`
- 提交前跑一遍：`python -m envswitch list` 与 `python build_exe.py --check`

## 报告 bug

请附带：操作系统与版本、Python 版本、`python -m envswitch doctor` 的输出、
复现步骤、预期与实际行为。
