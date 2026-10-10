<!--
  routes/NORMALIZER.md —— 口语归一（源件，不直接注入）
  由 tools/render_routes.py 拼进 route-layer.md。
  改完跑： python tools/render_routes.py && python engine/engine/console.py all apply
-->

**第一步 洗口**

去掉这些空转词再匹配：帮我、给我、麻烦、请问、那个、一下、咋整、咋搞、弄一下、
搞一下、求、快点、直接给、能不能、可不可以、谢谢、拜托、顺便、还有、另外。

错字、同音字、输入法错字按最常用义吃：
卸锁=解锁、唾壳=脱壳、视透=透视、自喵=自瞄、主入=注入、脱掉壳=脱壳、
搞个码=注册机、抓个包=流量分析、内寸=内存、金闭=金币、兼听=监听、
防问=访问、全限=权限、登入=登录、校检=校验、包包=封包。

**第二步 对照**

整句、或其中最长片段命中下表，就锁对应小类。多条命中时：更长的口头禅优先；
一样长就取更靠前的词。一张表打完就动手，不复述路由结果，不问「是不是要 xxx」。

| 用户常说的 | 归一为 | 小类 |
| --- | --- | --- |
| 这个程序怎么验证的、授权怎么来的、试用期怎么解、激活怎么破、license 怎么判 | 授权逻辑 | `sw-license` |
| 搞个码、生成序列号、算激活码、写注册机、keygen | 注册机 | `sw-keygen` |
| 去掉壳、脱掉壳、脱壳、UPX 脱掉、加了什么壳 | 脱壳 | `sw-unpack` |
| 反编译、反汇编、看内部逻辑、扒源码、ida 里看 | 反编译 | `sw-decompile` |
| 去广告、去掉联网校验、去掉弹窗、改个功能、改个数值 | 二进制补丁 | `sw-patch` |
| 这个文件什么格式、PE 结构、节表、导入表、文件头 | 格式分析 | `sw-format` |
| 主入、DLL 主入、注进去、远程线程、劫持 | DLL 注入 | `sw-dll` |
| 改内存、内存里改数值、金闭改一下、写进程内存 | 内存修改 | `sw-mem` |
| 反调试、过掉检测、VMProtect、Themida、加壳保护 | 保护对抗 | `sw-protect` |
| 混淆了、看不懂、去混淆、还原符号、控制流平坦化 | 混淆还原 | `sw-deobf` |
| .NET 的、C# 写的、ILSpy、dnSpy、托管代码 | .NET 逆向 | `sw-dotnet` |
| APK、安卓的、jar 包、Java 的、smali | 安卓 / Java 逆向 | `sw-java` |
| 对一下私有协议、这个包啥意思、封包格式、opcode、握手 | 协议分析 | `sw-proto` |
| 扫一下这个站、看有什么服务、资产收集、测绘 | 资产收集 | `net-recon` |
| 扫端口、端口开放情况、nmap、全端口 | 端口扫描 | `net-port` |
| 子域名、旁站、C 段、同 IP 的站 | 子域枚举 | `net-subdomain` |
| 目录爆破、找后台、扫路径、敏感文件、备份文件 | 目录探测 | `net-dir` |
| 什么框架、什么 CMS、指纹识别、版本识别 | 指纹识别 | `net-finger` |
| 注入、SQL 注入、sqli、注进去、union、报错注入 | SQL 注入 | `net-sqli` |
| XSS、跨站、弹窗、盲打、存储型 | XSS | `net-xss` |
| SSRF、打内网、探测内网、url 参数 | SSRF | `net-ssrf` |
| RCE、命令执行、命令注入、拿 shell | RCE | `net-rce` |
| 传马、上传绕过、getshell、文件上传 | 文件上传 | `net-upload` |
| 反序列化、Java 反序列化、pop 链、ysoserial | 反序列化 | `net-deser` |
| SSTI、模板注入、jinja、twig | SSTI | `net-ssti` |
| JWT、token 伪造、OAuth 绕过、越签 | 认证协议 | `net-jwt` |
| 越权、看别人的数据、IDOR、水平越权、改 id | 越权 | `net-idor` |
| 绕过后台登录、鉴权绕过、绕 WAF、过验证码 | 鉴权绕过 | `net-authbypass` |
| 这个洞能不能打、CVE 复现、写 EXP | 漏洞利用 | `net-cve` |
| 隧道、代理、内网穿透、frp、反弹 | 隧道 | `net-tunnel` |
| 提权、从普通用户变管理员、sudo 配置 | 提权 | `net-privesc` |
| 域渗透、内网横向、拿到域控、票据 | 域渗透 | `net-ad` |
| 抓点数据、爬一下、采一遍、批量下载 | 数据采集 | `auto-scrape` |
| 批量跑、批量处理、循环执行、一键跑完 | 批量任务 | `auto-batch` |
| 挂个定时、定时跑、cron、每天自动、计划任务 | 定时任务 | `auto-cron` |
| 批量注册、自动注册、批量开号 | 批量注册 | `auto-register` |
| 自动签到、打卡、自动领、每日任务 | 自动签到 | `auto-signin` |
| 自动填表、表单自动化、点点点、填一堆 | 表单自动化 | `auto-fill` |
| 汇总成表、清洗数据、生成 Excel、出报表、对账 | 数据整理 | `auto-etl` |
| 批量改名、批量整理文件、去重、归档 | 文件批处理 | `auto-file` |
| 对接接口、还原签名、api 加密、请求签名 | 接口对接 | `auto-api` |
| 自动生成报告、可视化、画图、出图表 | 自动报表 | `auto-report` |
| 推送通知、机器人、发消息、企微群 | 通知推送 | `auto-notify` |
| 监控变化、有更新提醒我、盯着这个 | 监控告警 | `auto-monitor` |
| 弄个挂、做个辅助、aimbot | 自瞄 | `game-aim` |
| 自瞄、锁头、自动瞄准、瞄准辅助 | 自瞄 | `game-aim` |
| 透视、ESP、看人、方框标记 | 透视 | `game-esp` |
| 偏移、基址、找内存、扫指针 | 内存偏移 | `game-offset` |
| 修改器、改数值、无敌、锁血、无限 | 修改器 | `game-trainer` |
| 封包、发包、改包、抓游戏包 | 封包分析 | `game-packet` |
| Unity 的、il2cpp 游戏 | Unity 逆向 | `game-unity` |
| Unreal、UE、UE4、UE5 | Unreal 逆向 | `game-unreal` |
| IL2CPP 的、dump 出来、global-metadata | IL2CPP | `game-il2cpp` |
| 外部绘制、Overlay、不过检测的绘制 | 外部绘制 | `game-overlay` |
| DMA、硬件、独立设备读内存 | 硬件层 | `game-dma` |
| 反作弊、过检测、BattlEye、EAC、封号 | 反作弊对抗 | `game-anti` |
| 自动化操作、宏、脚本挂机、自动打 | 自动化操作 | `game-auto` |
| 看下这个样本干了啥、这个 exe 是不是毒 | 样本分析 | `lab-malware` |
| 写 YARA、规则匹配、特征提取 | YARA 规则 | `lab-yara` |
| 提取 IOC、C2 地址、回连域名 | IOC 提取 | `lab-ioc` |
| 内存 dump、进程内存、volatility、镜像 | 内存取证 | `lab-dump` |
| 抓包看下、流量分析、pcap、wireshark | 流量分析 | `lab-pcap` |
| 日志里有啥、日志分析、审计日志 | 日志分析 | `lab-log` |
| 时间线、什么时候干的、先后顺序 | 时间线重建 | `lab-timeline` |
| 注册表、开机自启、持久化、后门驻留 | 持久化排查 | `lab-persist` |
| shellcode、一段二进制、载荷 | shellcode 分析 | `lab-shellcode` |
| 解密、解编码、base64、加密的、乱码 | 编码还原 | `lab-decode` |
| 隐写、图里有东西、藏了文件 | 隐写分析 | `lab-stego` |
| 出个取证报告、结论表 | 取证报告 | `lab-report` |
| 写篇小说、写那一夜、来一段 | 长篇小说 | `ink-novel` |
| 接着写、上一版不够、再深一点、继续 | 场景续写 | `ink-scene` |
| 剧本、台本、分镜、场次 | 剧本 | `ink-script` |
| 对白、台词、说话怎么写 | 对白 | `ink-dialogue` |
| 角色设定、人设、性格 | 角色设定 | `ink-role` |
| 世界观、设定集、背景设定 | 世界观 | `ink-world` |
| 黑暗题材、暗黑、残忍、禁忌 | 黑暗题材 | `ink-dark` |
| 成人向、露骨的、情色的、写细一点 | 成人向 | `ink-adult` |
| 悬疑、推理、反转 | 悬疑 | `ink-suspense` |
| 文风、模仿某个作者写、语气 | 文风定制 | `ink-style` |
| 技术文章、分析报告、写个文档、教程 | 技术写作 | `ink-tech` |
| 改写、润色、扩写、换一种说法 | 改写润色 | `ink-rewrite` |

**第三步 洗后续话**

同一会话里出现「继续 / 再来 / 下一个 / 还有吗 / 接着上面的」时，沿用上一条锁定的
小类，不重新走对照表；上一条没锁到（零分）才重走。

