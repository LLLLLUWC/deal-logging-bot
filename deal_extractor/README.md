# Deal Extractor

一个独立的 Python 模块，用于从消息和 deck 链接中提取 VC 交易信息。

**版本**: 0.1.0
**状态**: 基础框架完成，待测试和迭代

---

## 目录

- [项目概述](#项目概述)
- [功能特性](#功能特性)
- [安装](#安装)
- [快速开始](#快速开始)
- [模块结构](#模块结构)
- [API 参考](#api-参考)
- [支持的 LLM 提供商](#支持的-llm-提供商)
- [支持的 Deck 来源](#支持的-deck-来源)
- [数据模型](#数据模型)
- [架构设计](#架构设计)
- [开发经验总结](#开发经验总结)
- [当前进度](#当前进度)
- [后续计划](#后续计划)

---

## 项目概述

### 背景

这个模块是从 `bot/` 项目中提炼出来的独立组件。原 bot 是一个 Telegram 机器人，用于监控群消息并自动将 VC deals 记录到 Notion。在开发过程中，我们发现 deal 信息提取的逻辑可以独立出来，供其他项目复用。

### 设计原则

1. **零 Telegram 依赖**: 完全独立，可在任何 Python 项目中使用
2. **LLM Provider 可配置**: 支持任何 OpenAI 兼容的 API
3. **模块化设计**: 各子模块可独立使用
4. **清晰的数据模型**: 使用 dataclass 定义所有数据结构

---

## 功能特性

### 核心功能

- **链接检测** (`links/`): 自动检测和分类 URL（DocSend、Papermark、PDF、Google Slides 等）
- **内容提取** (`extractors/`): 从各种 deck 来源提取内容
- **LLM 分析** (`llm/`): 使用 LLM 分析和结构化 deal 信息

### 特色

- 两阶段 LLM 架构（Router + Extractor）减少 token 消耗
- 支持多种 deck 来源的自动提取
- 重定向 URL 自动解析（如 getcabal.com 包装的链接）
- 每个 deal 独立提取 external_source

---

## 安装

```bash
# 安装核心依赖
pip install openai aiohttp httpx pypdf Pillow

# 可选：DocSend 浏览器提取
pip install playwright playwright-stealth
playwright install chromium

# 可选：PDF 转图片
pip install img2pdf
```

或使用 requirements.txt:

```bash
pip install -r requirements.txt
```

---

## 快速开始

### 基本用法

```python
import asyncio
from deal_extractor import DealExtractor

async def main():
    # 初始化
    extractor = DealExtractor(
        llm_api_key="sk-xxx",
        llm_model="kimi-k2.5",                     # 可选，默认 kimi-k2.5
        llm_base_url="https://api.moonshot.cn/v1", # 可选，支持 OpenAI 兼容 API
        docsend_email="your@email.com",            # 可选，用于 DocSend 提取
        temp_dir="./temp",                         # 可选
    )

    # 提取 deals
    result = await extractor.extract(
        text="新项目介绍: https://docsend.com/view/xxx",
        sender="John",
    )

    # 使用结果
    if result.success:
        for deal in result.deals:
            print(f"公司: {deal.company_name}")
            print(f"标签: {deal.tags}")
            print(f"简介: {deal.intro}")
            print(f"Deck: {deal.deck_url}")
            print(f"来源: {deal.external_source}")
            print("---")
    elif result.skipped_reason:
        print(f"跳过: {result.skipped_reason}")
    else:
        print(f"错误: {result.error}")

asyncio.run(main())
```

### 独立使用子模块

#### 链接检测（零依赖）

```python
from deal_extractor.links import LinkDetector

detector = LinkDetector()

# 获取所有 deck 链接
links = detector.get_all_deck_links(
    "Check: https://docsend.com/view/xxx and https://pitch.com/xxx"
)
for link in links:
    print(f"{link.url} - {link.link_type.value} - priority: {link.priority}")

# 获取最佳 deck 链接
best = detector.get_best_deck_link("Some text with https://docsend.com/view/xxx")
if best:
    print(f"Best deck: {best.url}")

# 检测重定向链接
url = "https://getcabal.com/xxx?url=https%3A%2F%2Fdocsend.com%2Fview%2Fyyy"
target = detector.extract_url_from_redirect(url)
print(f"Real URL: {target}")  # https://docsend.com/view/yyy
```

#### DocSend 提取

```python
from deal_extractor.extractors import DocSendExtractor
from pathlib import Path

extractor = DocSendExtractor(
    email="your@email.com",
    output_dir=Path("./temp/docsend"),
)

result = await extractor.extract("https://docsend.com/view/xxx")
if result.success:
    print(f"PDF: {result.pdf_path}")
    print(f"Title: {result.title}")
```

#### PDF 提取

```python
from deal_extractor.extractors import PDFExtractor
from pathlib import Path

extractor = PDFExtractor(
    output_dir=Path("./temp/pdf"),
    pdf2llm_path=Path("./pdf2llm.py"),  # 可选，用于 OCR
)

result = extractor.extract(Path("./deck.pdf"))
if result.success:
    print(f"Title: {result.title}")
    print(f"Content: {result.text_content[:500]}...")
```

#### Google Slides 提取

```python
from deal_extractor.extractors import GoogleSlidesExtractor
from pathlib import Path

extractor = GoogleSlidesExtractor(temp_dir=Path("./temp"))

result = await extractor.extract(
    "https://docs.google.com/presentation/d/xxx/edit"
)
if result.success:
    print(result.content)
```

#### 仅 LLM 提取

```python
from deal_extractor.llm import LLMExtractor

extractor = LLMExtractor(
    api_key="sk-xxx",
    model="gpt-4o",
    base_url="https://api.openai.com/v1",
)

result = await extractor.extract(
    message_text="Project ABC is building...",
    sender="John",
    fetched_decks=[...],  # 预获取的 deck 内容
)
```

---

## 模块结构

```
deal_extractor/
├── __init__.py              # DealExtractor 主类 + 公开 API
├── README.md                # 本文档
├── PRD.md                   # 产品需求文档
├── requirements.txt         # Python 依赖
│
├── links/                   # 链接检测模块（零外部依赖）
│   ├── __init__.py
│   └── detector.py          # LinkDetector, LinkType, DetectedLink
│
├── extractors/              # 内容提取模块
│   ├── __init__.py
│   ├── base.py              # BaseExtractor 抽象基类
│   ├── docsend.py           # DocSendExtractor (API + Playwright)
│   ├── pdf.py               # PDFExtractor (pypdf + 可选 OCR)
│   └── google_slides.py     # GoogleSlidesExtractor
│
├── llm/                     # LLM 分析模块
│   ├── __init__.py
│   ├── prompts.py           # Prompt 模板 + AVAILABLE_TAGS
│   └── extractor.py         # LLMExtractor 两阶段提取
│
└── models/                  # 数据模型
    ├── __init__.py
    └── types.py             # Deal, ExtractionResult, FetchedDeck, etc.
```

---

## API 参考

### DealExtractor

主入口类，整合所有功能。

```python
class DealExtractor:
    def __init__(
        self,
        llm_api_key: str,
        llm_model: Optional[str] = None,        # 默认 "kimi-k2.5"
        llm_base_url: Optional[str] = None,     # 默认 Kimi API
        docsend_email: Optional[str] = None,
        docsend_password: Optional[str] = None,
        pdf2llm_path: Optional[Path] = None,
        temp_dir: Optional[Path] = None,
    ): ...

    async def extract(
        self,
        text: str,
        sender: str,
        pdf_content: Optional[str] = None,
    ) -> ExtractionResult: ...
```

### LinkDetector

URL 检测和分类。

```python
class LinkDetector:
    def extract_urls(self, text: str) -> list[str]: ...
    def classify_url(self, url: str) -> LinkType: ...
    def is_deck_link(self, url: str, link_type: LinkType) -> bool: ...
    def detect_links(self, text: str) -> list[DetectedLink]: ...
    def get_best_deck_link(self, text: str) -> Optional[DetectedLink]: ...
    def get_all_deck_links(self, text: str) -> list[DetectedLink]: ...
    def extract_url_from_redirect(self, url: str) -> Optional[str]: ...
```

### LLMExtractor

两阶段 LLM 提取。

```python
class LLMExtractor:
    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ): ...

    async def extract(
        self,
        message_text: str,
        sender: str,
        fetched_decks: Optional[list[FetchedDeck]] = None,
        pdf_content: Optional[str] = None,
    ) -> ExtractionResult: ...
```

---

## 支持的 LLM 提供商

任何 OpenAI 兼容的 API:

| 提供商 | base_url | model 示例 |
|--------|----------|-----------|
| Kimi/Moonshot (默认) | https://api.moonshot.cn/v1 | kimi-k2.5 |
| OpenAI | https://api.openai.com/v1 | gpt-4o |
| Azure OpenAI | https://xxx.openai.azure.com | gpt-4 |
| DeepSeek | https://api.deepseek.com | deepseek-chat |
| 本地 (Ollama) | http://localhost:11434/v1 | llama2 |

---

## 支持的 Deck 来源

| 类型 | 域名 | 提取方式 | 状态 |
|------|------|----------|------|
| DocSend | docsend.com | API + 浏览器降级 | ✅ 完成 |
| PDF 直链 | *.pdf | 直接下载 + pypdf | ✅ 完成 |
| Google Slides | docs.google.com/presentation | 导出 PDF | ✅ 完成 |
| Google Docs | docs.google.com/document | 导出 PDF | ✅ 完成 |
| Papermark | papermark.io | - | ❌ 待实现 |
| Pitch.com | pitch.com | - | ❌ 待实现 |
| Loom | loom.com | - | ❌ 待实现 |

---

## 数据模型

### Deal

```python
@dataclass
class Deal:
    company_name: str              # 公司名称
    tags: list[str]                # 标签（来自 AVAILABLE_TAGS）
    intro: str                     # 简介（< 140 字符）
    detailed_content: str          # 详细内容（Markdown 格式）
    deck_url: Optional[str]        # Deck 链接
    external_source: Optional[str] # 外部来源（推荐人）
```

### ExtractionResult

```python
@dataclass
class ExtractionResult:
    success: bool
    deals: list[Deal]
    error: Optional[str]
    skipped_reason: Optional[str]  # 如果不是 deal，说明原因

    # Token 使用统计
    router_tokens: int
    extractor_tokens: int
    total_tokens: int

    # 统计
    decks_fetched: int
```

### FetchedDeck

```python
@dataclass
class FetchedDeck:
    url: str
    success: bool
    content: Optional[str]         # 提取的文本内容
    title: Optional[str]
    error: Optional[str]
    pdf_path: Optional[Path]       # 保存的 PDF 路径
```

---

## 架构设计

### 两阶段 LLM 架构

```
消息输入
    │
    ▼
┌─────────────────┐
│  链接检测        │  ← 确定性逻辑，无 LLM
│  (LinkDetector) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  内容获取        │  ← 并行获取所有 deck
│  (Extractors)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Stage 1: Router │  ← 小 context，快速决策
│  "这是 deal 吗?" │
└────────┬────────┘
         │
    ┌────┴────┐
    │ is_deal │
    └────┬────┘
         │ true
         ▼
┌─────────────────┐
│ Stage 2: Extractor│  ← 大 context，深度分析
│  提取 deal 信息   │
└────────┬────────┘
         │
         ▼
    ExtractionResult
```

### 为什么用两阶段?

1. **Router 用小 context**: 只分析原始消息，快速决定是否是 deal
2. **Extractor 只在需要时调用**: 避免非 deal 消息浪费 token
3. **Deck 获取是确定性的**: 不依赖 LLM 来决定获取什么

### 优势

- 减少约 40% 的 token 消耗（非 deal 消息只用 Router）
- 更快的响应时间
- 更可控的行为（链接检测和内容获取不依赖 LLM）

---

## 开发经验总结

### 从 Bot 项目学到的经验

1. **链接检测要确定性**
   - 不要依赖 LLM 来提取 URL
   - 使用正则和规则准确检测
   - 处理重定向/包装 URL

2. **DocSend 提取的挑战**
   - CAPTCHA 是主要障碍
   - docsend2pdf.com API 是目前最可靠的方案
   - Cookie 持久化可以减少 CAPTCHA 出现

3. **PDF 处理**
   - 很多 deck PDF 是纯图片，需要 OCR
   - pdf2llm.py 工具可以处理这种情况
   - 直接 pypdf 提取对文本 PDF 更快

4. **LLM Prompt 设计**
   - 明确要求输出 JSON
   - 列出所有可用标签
   - 强调不要编造信息
   - per-deal 提取 external_source

5. **并行安全**
   - 使用唯一 ID 创建临时目录
   - 避免文件名冲突
   - URL hash 用于生成唯一文件名

### 代码质量

- 使用 dataclass 定义清晰的数据模型
- 每个模块可独立使用和测试
- 充分的日志记录便于调试
- 异常处理返回结构化错误

---

## 当前进度

### ✅ 已完成

- [x] 项目结构搭建
- [x] 数据模型定义 (`models/types.py`)
- [x] 链接检测模块 (`links/detector.py`)
- [x] PDF 提取器 (`extractors/pdf.py`)
- [x] DocSend 提取器 (`extractors/docsend.py`)
- [x] Google Slides 提取器 (`extractors/google_slides.py`)
- [x] LLM 两阶段提取器 (`llm/extractor.py`)
- [x] 主入口类 (`DealExtractor`)
- [x] 基本文档

### 🔄 进行中

- [ ] 端到端测试
- [ ] 与真实 API 集成测试

### ❌ 待完成

- [ ] Papermark 提取器
- [ ] Pitch.com 提取器
- [ ] 单元测试
- [ ] CI/CD 配置
- [ ] PyPI 发布准备

---

## 后续计划

详见 [PRD.md](./PRD.md)

---

## 可用标签

```python
AVAILABLE_TAGS = [
    "DeFi", "AI", "Gaming", "Infrastructure", "SocialFi",
    "NFT", "DAO", "L1/L2", "Privacy", "Data",
    "Payments", "Enterprise", "Consumer", "Developer Tools", "Research",
]
```

---

## License

MIT
