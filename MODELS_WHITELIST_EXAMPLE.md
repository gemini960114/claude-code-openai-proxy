# Claude Proxy 模型限制 (白名單) 設定範例

本 Proxy 支援兩種模型列出模式：**全開模式 (預設)** 與 **白名單限制模式**。以下說明如何設定。

---

## 1. 全開模式 (預設)

當你想讓外部工具（例如 Open WebUI、Claude Code 或其他客戶端）能夠存取後端 API 閘道器所支援的**所有模型**時，請在 `proxy.py` 中保持預設的萬用字元設定：

```python
# C:\claude-message-proxy\proxy.py (約第 17 行)
SUPPORTED_MODELS = ["*"]
```

**運作效果**：
- Proxy 會自動跟後端聯絡（例如拉取 Portal 的 50+ 個模型），並完整回傳給客戶端。

---

## 2. 白名單限制模式 (限制特定模型)

如果你只想開放部分模型給客戶端選擇（例如：只准使用特定的 NVIDIA 或是 GLM 模型，避免使用其他高成本的模型），可以直接修改 `SUPPORTED_MODELS` 陣列，手動寫入允許的模型 ID。

### 範例：只限制開放 Nemotron 與 MiniMax 兩台模型
```python
# C:\claude-message-proxy\proxy.py (約第 17 行)
SUPPORTED_MODELS = [
    "NVIDIA-Nemotron-3-Ultra-550B-A55B",
    "MiniMax-M2.7"
]
```

**運作效果**：
1. **清單過濾**：當客戶端呼叫 `/v1/models` 查詢模型時，Proxy 會向後端抓取全部模型，但**只篩選出**包含在上述列表中的模型回傳。因此客戶端介面只會顯示 `NVIDIA-Nemotron-3-Ultra-550B-A55B` 與 `MiniMax-M2.7`。
2. **斷網回退**：若突然無法連線到後端，Proxy 會直接回傳這份靜態白名單作為備用。

---

## 3. 常見白名單配置推薦

### 推薦配置 A：只保留主力模型與嵌入模型
```python
SUPPORTED_MODELS = [
    "NVIDIA-Nemotron-3-Ultra-550B-A55B",
    "GLM-5.2",
    "MiniMax-M2.7",
    "bge-m3" # 嵌入/向量模型
]
```

### 推薦配置 B：只允許開源 TAIDE / 國產 breeze 模型
```python
SUPPORTED_MODELS = [
    "Gemma-3-TAIDE-12b-Chat",
    "Llama-3.1-TAIDE-LX-8B-Chat",
    "Breeze-ASR-26"
]
```

---

## 4. 生效方式

修改 `C:\claude-message-proxy\proxy.py` 中的 `SUPPORTED_MODELS` 內容並存檔後：
1. **不需重新安裝**。
2. 如果你的 Uvicorn 是以開發模式運行（一般會自動偵測檔案變更重啟），服務會自動加載新設定；若是手動關閉，請重新執行 `start_proxy_inner.ps1` 或 `start_proxy_portal.ps1` 即可生效。
