# Auto Connect GPT · 连接守护流程

**阅读顺序：① 网络优先 → ② 必要时优选新加坡节点 → ③ 应用与 Remote → ④ 安全重启。**

蓝色为检查，黄色为受限恢复，绿色为通过，红色为停止/人工处理。拆成四张图，跨阶段只用明确出口衔接，不绘制贯穿全图的长回线。所有结束均记录结果，下一轮重新从网络检查开始。

## ① 网络优先

```mermaid
flowchart TD
    A([登录后立即 / 每小时 / 延后复检]) --> L{取得单实例锁?}
    L -->|否| X([跳过重复巡检])
    L -->|是| T[优先处理遗留节点回滚事务]
    T --> U{回滚完成或无需回滚?}
    U -->|否| M[保留现场 · 通知人工]
    U -->|是| S[记录防睡眠断言状态]
    S --> B{基础网络通过?}
    B -->|失败或未知| N[检查 Wi-Fi / 路由器 / 认证门户]
    B -->|是| C{Clash 内核及代理端口正常?}
    C -->|否| R[冷却允许时启动 Clash<br/>仍异常则正常重启一次]
    R --> RQ{内核与端口恢复?}
    RQ -->|否或冷却限制| M
    RQ -->|是| P
    C -->|是| P{双目标代理探测结果?}
    P -->|通过| OK([进入 ③ 应用与 Remote])
    P -->|4xx / 5xx 等未知| Q[报告无法确认<br/>不切节点 · 不重启 GPT]
    P -->|传输失败| W[等待 10 秒再探测]
    W --> V{复检结果?}
    V -->|通过| OK
    V -->|未知| Q
    V -->|仍失败| SG([进入 ② 新加坡节点优选])
    classDef inspect fill:#eff6ff,stroke:#3b82f6,color:#172554;
    classDef action fill:#fffbeb,stroke:#f59e0b,color:#78350f;
    classDef stop fill:#fff1f2,stroke:#e11d48,color:#881337;
    classDef good fill:#ecfdf5,stroke:#10b981,color:#064e3b;
    class L,U,S,B,C,P,V inspect;
    class T,R,W action;
    class M,N,Q stop;
    class OK,SG good;
```

## ② 新加坡节点优选

```mermaid
flowchart TD
    A([代理传输持续故障]) --> C{配置 / 控制接口 / 冷却均允许?}
    C -->|否| E[保留原选择 · 提示原因]
    C -->|是| F[筛选指定 Selector 的新加坡叶子节点<br/>排除当前节点 / DIRECT / REJECT / 嵌套组]
    F --> Q{有候选且数量在上限内?}
    Q -->|否| E
    Q -->|是| D[持久化本轮额度<br/>每候选默认 3 轮双目标测试]
    D --> R[淘汰任意测试失败节点<br/>按 ChatGPT 延迟中位数升序排列]
    R --> H{有通过测试的节点?}
    H -->|否| E
    H -->|是| N[从最低延迟开始<br/>每次操作前保存可恢复事务]
    N --> K{组选择仍未被外部修改?}
    K -->|否| E
    K -->|是| SW[切换一个候选并回读确认]
    SW --> V{经实际代理连续两次验证通过?}
    V -->|是| OK([保留新节点 · 进入 ③])
    V -->|否| RB[恢复原节点]
    RB --> B{回滚确认成功?}
    B -->|否| MAN[停止一切自动恢复<br/>保留事务 · 通知人工]
    B -->|是| NEXT{还有候选且本轮不足 3 次?}
    NEXT -->|是| N
    NEXT -->|否| E
    classDef inspect fill:#eff6ff,stroke:#3b82f6,color:#172554;
    classDef action fill:#fffbeb,stroke:#f59e0b,color:#78350f;
    classDef stop fill:#fff1f2,stroke:#e11d48,color:#881337;
    classDef good fill:#ecfdf5,stroke:#10b981,color:#064e3b;
    class C,Q,H,K,V,B,NEXT inspect;
    class F,D,R,N,SW,RB action;
    class E,MAN stop;
    class OK good;
```

这里的“最低延迟”限定于本轮配置范围内、全部测试合格的候选。最快者切换后验证不通过，则尝试次快者。切换批次冷却 6 小时；候选组须实际影响目标流量，程序不擅改路由规则。

## ③ 应用与 Remote

```mermaid
flowchart TD
    A([VPN / 代理已通过]) --> P{ChatGPT 主进程存在?}
    P -->|否| START[额度允许时启动<br/>等待 45 秒 · 全链路复检]
    START --> END[记录结果 · 不再重复恢复]
    P -->|是| S{属于当前主进程的 App Server 正常?}
    S -->|否| WAIT[等待 60 秒 · 同实例复检]
    WAIT --> SQ{仍缺失且实例未变?}
    SQ -->|是| SAFE([进入 ④ 重启安全门])
    SQ -->|实例已变| UNKNOWN[无法确认 · 不重启]
    SQ -->|已恢复| R
    S -->|是| R{当前实例的新鲜 Remote 证据?}
    R -->|在线| OK([通过 · 记录状态])
    R -->|登录或配对问题| MAN[人工登录 / 配对 / 启用 Remote]
    R -->|未知| RETRY[间隔 60 秒复查 · 最多两次]
    RETRY --> RR{复查结果?}
    RR -->|仍未知或实例变化| UNKNOWN
    RR -->|在线| OK
    RR -->|登录或配对问题| MAN
    RR -->|明确失败| FAIL
    R -->|明确失败| FAIL[等待 60 秒获取新的独立证据]
    FAIL --> FQ{同实例第二次独立失败?}
    FQ -->|是| SAFE
    FQ -->|已在线| OK
    FQ -->|登录或配对问题| MAN
    FQ -->|其他 / 同一旧证据| UNKNOWN
    classDef inspect fill:#eff6ff,stroke:#3b82f6,color:#172554;
    classDef action fill:#fffbeb,stroke:#f59e0b,color:#78350f;
    classDef stop fill:#fff1f2,stroke:#e11d48,color:#881337;
    classDef good fill:#ecfdf5,stroke:#10b981,color:#064e3b;
    class P,S,SQ,R,RR,FQ inspect;
    class START,WAIT,RETRY,FAIL action;
    class UNKNOWN,MAN stop;
    class OK,SAFE good;
```

Remote 没有证据 ≠ Remote 离线。默认未接入采集器时，此阶段报告未知；不得用 App Server 存在替代远程可用性。应用主进程变化时旧证据作废。

## ④ 重启安全门与闭环

```mermaid
flowchart TD
    A([应用或 Remote 持续异常]) --> N{代理通过 / 实例未变<br/>且无明确登录配对问题?}
    N -->|否| STOP[取消重启 · 保留现场]
    N -->|是| T{30 秒内的任务证据明确空闲?}
    T -->|忙碌或未知| D{延后次数不足两次?}
    D -->|是| L[10 分钟后从网络阶段重查]
    D -->|否| MAN[通知人工确认任务与连接]
    T -->|是| C{6 小时冷却及 2 次 / 24h 额度允许?}
    C -->|否| STOP
    C -->|是| SAVE[动作前持久化额度]
    SAVE --> Q[正常退出 ChatGPT<br/>不退出账号 · 不强制杀进程]
    Q --> EXIT{正常退出成功?}
    EXIT -->|否| MAN
    EXIT -->|是| OPEN[重新打开 · 等待 45 秒]
    OPEN --> V[重新检查代理 / 应用 / App Server / Remote]
    V --> RESULT{全链路检查及 Remote 证据通过?}
    RESULT -->|是| OK[记录通过 · 状态变化时通知]
    RESULT -->|失败或未知| MAN
    classDef inspect fill:#eff6ff,stroke:#3b82f6,color:#172554;
    classDef action fill:#fffbeb,stroke:#f59e0b,color:#78350f;
    classDef stop fill:#fff1f2,stroke:#e11d48,color:#881337;
    classDef good fill:#ecfdf5,stroke:#10b981,color:#064e3b;
    class N,T,D,C,EXIT,RESULT inspect;
    class L,SAVE,Q,OPEN,V action;
    class STOP,MAN stop;
    class OK good;
```

每轮应用恢复最多一次，失败也占用额度。单轮约 15 分钟时间预算；恢复动作结束后回到正常定时巡检，不用无上限回路重复切换或重启。`--diagnose` 只读模式跳过全部恢复动作和等待复检。
