# 📈 金融日报 | Financial Daily

每日由 Claude API + Web Search 自动汇总七大模块：**全球指数** · **领袖观点** · **市场要闻** · **宏观数据** · **财报追踪** · **行业轮动** · **央行动态**

## 在线访问

**主站：** https://yang1bai.github.io/finance-daily-site/

**历史归档：** https://yang1bai.github.io/finance-daily-site/archive/

**RSS 订阅：** https://yang1bai.github.io/finance-daily-site/feed.xml

---

## 功能模块

| 模块 | 说明 |
|------|------|
| 💹 **全球指数** | S&P 500 / NASDAQ / DOW / 上证 / 恒生 / 日经 / DAX / 黄金 / 原油 / BTC |
| 👔 **领袖观点** | 巴菲特、达利欧、戴蒙等金融领袖最新言论 |
| 📰 **市场要闻** | 过去 24-48 小时重磅市场新闻，含原文链接 |
| 📊 **宏观数据** | CPI、NFP、PCE、GDP、PMI 等关键经济指标 |
| 💼 **财报追踪** | 本周主要公司财报结果（超预期/不及预期） |
| 🏭 **行业轮动** | S&P 500 十一大行业涨跌表现 |
| 🏦 **央行动态** | 美联储、欧央行、中国人民银行、日央行最新动态 |

---

## 架构

```
.
├── index.html                    # 主页（Bloomberg 风格暗色 UI）
├── feed.xml                      # RSS/Atom 订阅（自动生成）
├── .nojekyll
├── README.md
├── data/
│   ├── index.json                # 归档日期列表（自动生成）
│   ├── latest.json               # 最新一期原始 JSON
│   └── YYYY-MM-DD.json           # 每日原始数据归档
├── archive/
│   ├── index.html                # 归档目录页（自动生成）
│   └── YYYY-MM-DD.html           # 每日 HTML 快照
├── scripts/
│   ├── fetch_content.py          # 调用 Claude API + web_search 抓取内容
│   └── icons.py                  # SVG 图标定义
└── .github/workflows/update.yml  # 每日两次 cron 触发，自动 commit
```

---

## 部署清单（按顺序操作一次即可）

### 1. 推送代码到仓库

将本目录所有文件推送到你的 GitHub 仓库 `main` 分支。注意 `.nojekyll` 是隐藏文件别遗漏。

```bash
git init
git add -A
git commit -m "init: finance-daily-site"
git remote add origin https://github.com/Yang1Bai/finance-daily-site.git
git push -u origin main
```

### 2. 配置 Anthropic API Key

仓库 → **Settings → Secrets and variables → Actions** → **New repository secret**：

- **Name**：`ANTHROPIC_API_KEY`
- **Secret**：粘贴你的 API key（以 `sk-ant-` 开头）

### 3. 打开 Actions 写权限

仓库 → **Settings → Actions → General** → 滚到底 → **Workflow permissions** → 选 **Read and write permissions** → Save。

### 4. 启用 GitHub Pages

仓库 → **Settings → Pages** → Source 选 *Deploy from a branch* → 分支 `main` / `(root)` → Save。

### 5. 手动触发一次验证

仓库 → **Actions** → 左侧 **Daily Financial Dashboard Update** → 右上 **Run workflow** → 点绿色按钮。等 1-2 分钟看到绿色 ✅ 后访问网站。

---

## 成本估算

模型默认 `claude-sonnet-4-5`，启用 `web_search`（最多 15 次）：

| 运行频率 | 单次成本 | 月费用 |
|---------|---------|--------|
| 工作日 2次/天 + 周末 1次/天 | ~$0.20-0.30 | **~$7-9/月** |
| 仅工作日 1次/天 | ~$0.20-0.30 | **~$4-5/月** |

如想降低成本，将 `update.yml` 中的 `ANTHROPIC_MODEL` 改为 `claude-haiku-4-5`，可降至约 **$1-2/月**。

---

## 自定义

- **调整内容偏好**：编辑 `scripts/fetch_content.py` 中的 `USER_PROMPT_TEMPLATE`
- **修改运行时间**：编辑 `.github/workflows/update.yml` 中的 `cron` 表达式
- **添加/删除模块**：在 `index.html` 中添加对应注释锚点，并在 `fetch_content.py` 中添加渲染函数

---

## 已知限制

- 指数数据依赖 Claude web_search，非实时报价（有数分钟延迟）
- 新闻 URL 为 Claude 搜索结果，偶尔可能失效
- 财报 EPS 数据以搜索结果为准，如需精确数据建议接入 Financial Modeling Prep 等 API
