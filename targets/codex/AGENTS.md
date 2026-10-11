## Windows约束

当前环境是 Windows 11 / pwsh7

- 默认禁止使用 Bash 语法，除非确定此shell处在 Linux 环境

- 不要使用 Bash 引号/转义习惯，在 PowerShell 命令里，复杂正则优先用单引号包裹。

- 如果正则本身同时包含单引号和双引号，优先拆成多个简单 rg 命令。

- 执行多行 Python 禁止使用 Bash heredoc；改用 PowerShell here-string | python -

- pwsh 中，语句块表达式（如 `foreach`、`if`）不能直接作为管道输入。 需要先使用 `$()` / `@()` 包裹，或先赋值给变量。 普通命令输出可直接进入管道，无需额外包裹。

- PowerShell 使用 `rg` 时，通配目录必须先用 `Get-ChildItem -Filter` 展开为真实路径，禁止直接把含 `*` 的搜索路径传给 `rg`。

---
