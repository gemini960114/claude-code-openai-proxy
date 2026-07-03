# Claude Desktop 第三方 Gateway 設定文件

本文件說明如何在 **Claude Desktop** 中使用第三方 inference gateway，將 Claude Desktop 的請求導向本機 FastAPI proxy，再由 proxy 轉接至後端 OpenAI-compatible API（支援 **Inner-Medusa** 與 **Portal** 模式）。

## 一、Gateway 基本設定

| 設定項目                | 欄位名稱                | 填寫內容                    | 說明                                                    |
| ------------------- | ------------------- | ----------------------- | ----------------------------------------------------- |
| 連線類型                | Connection          | `Gateway`               | 使用第三方 inference gateway。                              |
| Gateway Base URL    | Gateway base URL    | `http://127.0.0.1:5000` | 本機 FastAPI proxy 的服務位址。Claude Desktop 會將推論請求送到此位址。    |
| Gateway API Key     | Gateway API key     | `anything`              | 傳給 Gateway 的 API key。因本機 proxy 實際使用 `.env` 內的 API key 向後端驗證，在此處填入任意值即可。 |
| Gateway Auth Scheme | Gateway auth scheme | `bearer`                | 表示 API key 會以 `Authorization: Bearer <API_KEY>` 形式送出。 |
| Credential Kind     | Credential kind     | `Static API key`        | 使用固定 API key，不從其他來源自動取得。                              |

---

## 二、Custom Inference Headers 設定

| Header Name     | Value             | 說明                                                                                             |
| --------------- | ----------------- | ---------------------------------------------------------------------------------------------- |
| `Authorization` | `Bearer anything` | 額外指定 Authorization header。若已在 Gateway credentials 中設定 API key 與 bearer scheme，這項可視情況省略，避免重複送出。 |

建議設定方式：

| 情境                                                    | 建議                                                         |
| ----------------------------------------------------- | ---------------------------------------------------------- |
| FastAPI proxy 需要讀取 `Authorization` header             | 保留 `Authorization: Bearer anything`                        |
| Claude Desktop 已透過 Gateway API key 自動送出 Authorization | 可移除 Custom inference headers 中的 Authorization              |
| FastAPI proxy 不驗證使用者端 API key                         | Gateway API key 可填 `anything`，Custom inference headers 可留空 |

---

## 三、Model Discovery 設定

| 設定項目            | 填寫內容                              | 說明                                              |
| --------------- | --------------------------------- | ----------------------------------------------- |
| Model discovery | 開啟或關閉皆可                           | 若 FastAPI proxy 有提供 `GET /v1/models`，可開啟自動探索模型。 |
| Discovery URL   | `http://127.0.0.1:5000/v1/models` | Claude Desktop 會在啟動時從此端點讀取目前設定檔內的模型清單。                 |
| Model list      | 手動設定                              | 即使開啟 Model discovery，也可以用 Model list 覆寫模型清單。    |

建議：

| 狀況                                       | 建議設定                               |
| ---------------------------------------- | ---------------------------------- |
| `/v1/models` 已正確回傳對應的模型清單         | 可以開啟 Model discovery               |
| `/v1/models` 抓不到模型或顯示 found 0 models     | 關閉 Model discovery，改用手動 Model list |
| 需要固定模型對應                                 | 使用手動 Model list 較穩定                |

---

## 四、Model List 設定

Claude Desktop 的 **Model ID** 建議使用與 Proxy 別名 (aliases) 相容的 model ID，後端真實模型名稱與資訊則放在 **Display name**。
實際模型轉換由 FastAPI proxy 讀取設定檔負責處理。

### (1) Inner-Medusa 模式 (參照 `models_inner.json`)

| 順序 | Model ID            | Display name                        | Tier alias | Offer 1M-context variant | 說明                                                                |
| -- | ------------------- | ----------------------------------- | ---------- | ------------------------ | ----------------------------------------------------------------- |
| 1  | `claude-haiku-4-5`  | `GLM-5.2`                           | `haiku`    | 關閉                       | 作為 Haiku tier 的替代模型，實際後端模型為 `GLM-5.2`。                            |
| 2  | `claude-sonnet-4-6` | `NVIDIA-Nemotron-3-Ultra-550B-A55B` | `sonnet`   | 關閉                       | 作為 Sonnet tier 的替代模型，實際後端模型為 `NVIDIA-Nemotron-3-Ultra-550B-A55B`。 |
| 3  | `claude-opus-4-7`   | `Thanos3.5-397B-A17B`               | `opus`     | 關閉                       | 作為 Opus tier 的替代模型，實際後端模型為 `Thanos3.5-397B-A17B`。                 |

### (2) Portal 模式 (參照 `models_portal.json`)

| 順序 | Model ID            | Display name                        | Tier alias | Offer 1M-context variant | 說明                                                                |
| -- | ------------------- | ----------------------------------- | ---------- | ------------------------ | ----------------------------------------------------------------- |
| 1  | `claude-haiku-4-5`  | `gemma-4-31B-it`                    | `haiku`    | 關閉                       | 作為 Haiku tier 的替代模型，實際後端模型為 `gemma-4-31B-it`。                     |
| 2  | `claude-sonnet-4-6` | `NVIDIA-Nemotron-3-Ultra-550B-A55B` | `sonnet`   | 關閉                       | 作為 Sonnet tier 的替代模型，實際後端模型為 `NVIDIA-Nemotron-3-Ultra-550B-A55B`。 |
| 3  | `claude-opus-4-7`   | `Mistral-Large-3-675B-Instruct-2512` | `opus`     | 關閉                       | 作為 Opus tier 的替代模型，實際後端模型為 `Mistral-Large-3-675B-Instruct-2512`。  |
| 4  | `nemotron-super`    | `NVIDIA-Nemotron-3-Super-120B-A12B` | 留空 / 無    | 關閉                       | 額外模型，實際後端模型為 `NVIDIA-Nemotron-3-Super-120B-A12B`。                     |

---

## 五、FastAPI Proxy 端模型對應

本專案之 FastAPI proxy 會動態載入 [models_inner.json](models_inner.json) 或 [models_portal.json](models_portal.json)。
Claude Desktop 送出的模型名稱會是上面的 Model ID，Proxy 在接收到請求後，會比對別名（並自動忽略 `-YYYYMMDD` 日期後綴），轉換為真實的 `backend_model`。

### 動態比對邏輯 (`proxy.py`)

```python
def map_model(model: str) -> str:
    # 自動忽略類似 -20251001 的日期後綴
    normalized_model = normalize_model_id(model) 
    
    # 遍歷 JSON 設定檔中所有的模型與別名
    for entry in model_entries():
        names = [entry.get("id"), *entry.get("aliases", [])]
        if model in names or normalized_model in names:
            return entry.get("backend_model", model)
    return model
```

因此不需在程式碼中寫死對應表，如需修改對應，僅需調整 `models_inner.json` 或 `models_portal.json` 並重啟 Proxy 即可。

---

## 六、整體請求流程

| 步驟 | 元件             | 說明                                                                       |
| -- | -------------- | ------------------------------------------------------------------------ |
| 1  | Claude Desktop | 使用 Gateway 模式發送 Anthropic Messages API 格式請求。                             |
| 2  | FastAPI Proxy  | 接收 `POST /v1/messages` 請求。                                               |
| 3  | FastAPI Proxy  | 藉由載入的 JSON 設定檔將 Model ID 轉成後端真實模型名稱（例如 `gemma-4-31B-it`）。      |
| 4  | FastAPI Proxy  | 將 Anthropic Messages API 格式轉換成 OpenAI-compatible `/chat/completions` 格式。 |
| 5  | 後端 API         | 讀取環境變數中的後端 API Key 並執行模型推論。                                            |
| 6  | FastAPI Proxy  | 將 OpenAI-compatible 回應轉回 Anthropic Messages API 格式。                      |
| 7  | Claude Desktop | 顯示模型回應。                                                                  |

---

## 七、建議最終設定摘要

### (1) Inner-Medusa 模式
* **Gateway base URL**: `http://127.0.0.1:5000`
* **Default model**: `claude-haiku-4-5`
* **Default display name**: `GLM-5.2`
* **Default tier alias**: `haiku`

### (2) Portal 模式
* **Gateway base URL**: `http://127.0.0.1:5000`
* **Default model**: `claude-haiku-4-5`
* **Default display name**: `gemma-4-31B-it`
* **Default tier alias**: `haiku`

---

## 八、測試指令

設定完成後，可先用 PowerShell 測試 FastAPI proxy 模型對應與後端轉接是否正常：

```powershell
curl.exe -X POST "http://127.0.0.1:5000/v1/messages" `
  -H "Content-Type: application/json" `
  -H "x-api-key: anything" `
  -H "anthropic-version: 2023-06-01" `
  -d "{\"model\":\"claude-haiku-4-5\",\"max_tokens\":128,\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}"
```

若 Proxy 運作正常，您將會收到來自後端模型的 JSON 回應，且 Proxy 的終端機/日誌中應會顯示對應成功的日誌：

```text
[proxy] requested_model=claude-haiku-4-5 backend_model=gemma-4-31B-it (或 GLM-5.2)
```
