"""内置工具定义。

这里的每一条本质上只是"一份配置"——和用户自己在 GUI 里新建的自定义工具完全同构。
想加内置支持（比如 Rust、PHP、Ruby），照着抄一条加到 BUILTIN_TOOLS 即可，不需要改任何逻辑代码。
"""

from __future__ import annotations

from .models import ToolDef

# --------------------------------------------------------------------------- #
# Java / JDK
# --------------------------------------------------------------------------- #

JAVA = ToolDef(
    id="java",
    name="Java (JDK)",
    icon="☕",
    entry="dir",
    home_var="JAVA_HOME",
    extra_vars={"JDK_HOME": "{home}"},
    detect="bin/java{ext}",
    bin_names=["java"],
    version_cmd=["{bin}", "-version"],
    version_regex=r'"([^"]+)"',
    path_entries=["{home}/bin"],
    conflict_keywords=["java", "jdk", "jre"],
    project_file=".java-version",
    project_value="major",
    search_roots={
        "windows": [
            r"C:\Program Files\Java",
            r"C:\Program Files (x86)\Java",
            r"C:\Program Files\Eclipse Adoptium",
            r"C:\Program Files\Microsoft",
            r"C:\Program Files\Amazon Corretto",
            r"C:\Program Files\Zulu",
            r"C:\Program Files\BellSoft",
            r"C:\Program Files\GraalVM",
            r"C:\Program Files\JetBrains",
            r"C:\Program Files\Android\Android Studio\jbr",
            r"~\.jdks",
            r"~\scoop\apps",
            r"~\AppData\Local\Programs",
        ],
        "macos": [
            "/Library/Java/JavaVirtualMachines",
            "/System/Library/Java/JavaVirtualMachines",
            "~/Library/Java/JavaVirtualMachines",
            "/opt/homebrew/opt",
            "/usr/local/opt",
            "~/.sdkman/candidates/java",
            "~/.jdks",
            "~/.asdf/installs/java",
        ],
        "linux": [
            "/usr/lib/jvm",
            "/usr/java",
            "/usr/local/java",
            "/opt/java",
            "/opt/jdk",
            "~/.jdks",
            "~/.sdkman/candidates/java",
            "~/.asdf/installs/java",
        ],
    },
    builtin=True,
    notes="切换后写入 JAVA_HOME/JDK_HOME 并把 %JAVA_HOME%\\bin 前置到 PATH。",
)

# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #

PYTHON = ToolDef(
    id="python",
    name="Python",
    icon="🐍",
    entry="file",
    home_var="PYTHON_HOME",
    file_pattern=r"python[\d.]*(\.exe)?",
    bin_names=["python", "python3"],
    version_cmd=["{bin}", "--version"],
    version_regex=r"(\d+\.\d+\.\d+)",
    path_entries=["{home}", "{home}/Scripts", "{home}/bin"],
    # 注意：这里**不能**放 "scripts"。
    # C:\Python314\Scripts 是 pip 的所在地，但它离 C:\Python314 只有一层，
    # 命中 "scripts" 会让它被当成"抢在 Python 前面的同类条目"而在"一键修复"里被删掉 ——
    # 用户表现就是"修完之后 pip 没了"。判定抢路要用 provides_command() 看它到底能不能
    # 提供 python.exe，而不是拿目录名做子串匹配。
    conflict_keywords=["python", "pyenv", "conda"],
    project_file=".python-version",
    project_value="full",
    search_roots={
        "windows": [
            r"C:\Python",
            r"C:\Python*",          # python.org 安装包的默认位置：C:\Python314 这种
            r"C:\Program Files\Python",
            r"C:\Program Files\Python*",
            r"~\AppData\Local\Programs\Python",
            r"~\AppData\Local\Programs\Python\*",
            r"~\.pyenv\pyenv-win\versions",
            r"~\scoop\apps\python",
            r"~\scoop\apps\python\*",
            r"~\anaconda3",
            r"~\miniconda3",
            r"~\AppData\Local\miniconda3",
            r"~\AppData\Local\anaconda3",
            r"~\miniforge3",
        ],
        "macos": [
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/opt/local/bin",
            "/usr/bin",
            "~/.pyenv/versions",
            "~/anaconda3/bin",
            "~/miniconda3/bin",
            "~/opt/anaconda3/bin",
            "~/.local/bin",
        ],
        "linux": [
            "/usr/bin",
            "/usr/local/bin",
            "~/.pyenv/versions",
            "/opt/conda/bin",
            "~/anaconda3/bin",
            "~/miniconda3/bin",
            "~/.local/bin",
        ],
    },
    builtin=True,
    notes="Windows 下同时把 Scripts 目录加入 PATH，pip 安装的命令行工具可直接用。",
)

# --------------------------------------------------------------------------- #
# Node.js
# --------------------------------------------------------------------------- #

NODE = ToolDef(
    id="node",
    name="Node.js",
    icon="⬢",
    entry="file",
    home_var="NODE_HOME",
    file_pattern=r"node(\.exe)?",
    bin_names=["node"],
    version_cmd=["{bin}", "--version"],
    version_regex=r"v?(\d+\.\d+\.\d+)",
    path_entries=["{home}"],
    conflict_keywords=["nodejs", "nvm", "\\node", "/node"],
    project_file=".nvmrc",
    project_value="full",
    search_roots={
        "windows": [
            r"C:\Program Files\nodejs",
            r"~\scoop\apps\nodejs",
            r"~\scoop\apps\nodejs-lts",
            r"~\AppData\Local\Programs",
            r"~\AppData\Roaming\nvm",
            r"~\.nvm",
        ],
        "macos": [
            "~/.nvm/versions/node",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "~/.volta/tools/image/node",
            "/usr/local/n/versions/node",
            "~/.local/bin",
        ],
        "linux": [
            "~/.nvm/versions/node",
            "/usr/local/bin",
            "/usr/bin",
            "~/.volta/tools/image/node",
            "/usr/local/n/versions/node",
            "~/.local/bin",
        ],
    },
    builtin=True,
    notes="nvm / fnm / volta 装的各版本都能直接识别。",
)

# --------------------------------------------------------------------------- #
# Go
# --------------------------------------------------------------------------- #

GO = ToolDef(
    id="go",
    name="Go",
    icon="🐹",
    entry="dir",
    home_var="GOROOT",
    detect="bin/go{ext}",
    bin_names=["go"],
    version_cmd=["{bin}", "version"],
    version_regex=r"go(\d+\.\d+(?:\.\d+)?)",
    path_entries=["{home}/bin"],
    conflict_keywords=["\\go\\bin", "/go/bin", "golang"],
    project_file=".go-version",
    project_value="full",
    search_roots={
        "windows": [
            r"C:\Program Files\Go",
            r"~\scoop\apps\go",
            r"~\sdk",
            r"~\go",
        ],
        "macos": [
            "/usr/local/go",
            "/opt/homebrew/opt/go/libexec",
            "~/sdk",
            "~/.gvm/gos",
            "/usr/local/opt/go/libexec",
        ],
        "linux": [
            "/usr/local/go",
            "/usr/lib/go",
            "~/sdk",
            "~/.gvm/gos",
            "/usr/lib/go-*",
        ],
    },
    builtin=True,
    notes="切换后写入 GOROOT 并把 $GOROOT/bin 前置到 PATH。",
)

# --------------------------------------------------------------------------- #
# Maven
# --------------------------------------------------------------------------- #

MAVEN = ToolDef(
    id="maven",
    name="Maven",
    icon="🅼",
    entry="dir",
    home_var="MAVEN_HOME",
    extra_vars={"M2_HOME": "{home}"},
    detect="bin/mvn{ext}",
    bin_names=["mvn"],
    version_cmd=["{bin}", "-v"],
    version_regex=r"Apache Maven (\d+[\d.]*)",
    path_entries=["{home}/bin"],
    conflict_keywords=["maven"],
    project_file="",
    search_roots={
        "windows": [
            r"C:\Program Files\Apache",
            r"~\scoop\apps\maven",
            r"~\AppData\Local\Programs",
            "C:\\maven",
        ],
        "macos": ["/opt/homebrew/opt", "/usr/local/opt", "/opt", "~/opt", "~/.sdkman/candidates/maven"],
        "linux": ["/opt", "/usr/local", "/usr/share", "~/.sdkman/candidates/maven"],
    },
    builtin=True,
)

# --------------------------------------------------------------------------- #
# Gradle
# --------------------------------------------------------------------------- #

GRADLE = ToolDef(
    id="gradle",
    name="Gradle",
    icon="🅶",
    entry="dir",
    home_var="GRADLE_HOME",
    detect="bin/gradle{ext}",
    bin_names=["gradle"],
    version_cmd=["{bin}", "--version"],
    version_regex=r"Gradle (\d+[\d.]*)",
    path_entries=["{home}/bin"],
    conflict_keywords=["gradle"],
    project_file="",
    search_roots={
        "windows": [
            r"C:\Program Files\Gradle",
            r"~\scoop\apps\gradle",
            r"~\gradle",
            r"~\.gradle\wrapper\d*\0",
        ],
        "macos": ["/opt/homebrew/opt", "/usr/local/opt", "~/gradle", "~/.sdkman/candidates/gradle"],
        "linux": ["/opt", "/usr/local", "~/gradle", "~/.sdkman/candidates/gradle"],
    },
    builtin=True,
)

# --------------------------------------------------------------------------- #
# .NET SDK
# --------------------------------------------------------------------------- #

DOTNET = ToolDef(
    id="dotnet",
    name=".NET SDK",
    icon="🅽",
    entry="file",
    home_var="DOTNET_ROOT",
    file_pattern=r"dotnet(\.exe)?",
    bin_names=["dotnet"],
    version_cmd=["{bin}", "--version"],
    version_regex=r"(\d+\.\d+\.\d+)",
    path_entries=["{home}"],
    conflict_keywords=["dotnet"],
    project_file="global.json",
    search_roots={
        "windows": [
            r"C:\Program Files\dotnet",
            r"~\scoop\apps\dotnet-sdk",
            r"~\AppData\Local\Microsoft\dotnet",
        ],
        "macos": ["/usr/local/share/dotnet", "/opt/homebrew/share/dotnet", "~/.dotnet"],
        "linux": ["/usr/share/dotnet", "/usr/lib/dotnet", "~/.dotnet"],
    },
    builtin=True,
)


BUILTIN_TOOLS = [JAVA, PYTHON, NODE, GO, MAVEN, GRADLE, DOTNET]

#: 新建自定义工具时可选的预设模板（把内置定义的扫描逻辑借过来）
TEMPLATES = {
    "空模板": None,
    "类 Java（目录 + 主目录变量 + bin 目录）": JAVA,
    "类 Python（可执行文件 + Scripts 目录）": PYTHON,
    "类 Node（可执行文件，直接前置所在目录）": NODE,
    "类 Go（目录 + 主目录变量）": GO,
}
