import httpx
import json

def test_tool_call():
    # 本機 Proxy 網址
    url = "http://127.0.0.1:5000/v1/messages"
    
    headers = {
        "x-api-key": "anything",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    
    # 測試的 Model 名稱 (請確認此 model 存在於你的設定檔中，如 gemma-4-31B-it 或 MiniMax-M2.7)
    # 這裡預設使用 "haiku" 這個別名，會自動由 Proxy 對應到設定檔中的真實 Haiku 模型
    payload = {
        "model": "haiku",
        "max_tokens": 512,
        "messages": [
            {"role": "user", "content": "用 get_weather 查詢台北的天氣。"}
        ],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get current weather info for a location",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city name, e.g. Taipei"
                        }
                    },
                    "required": ["location"]
                }
            }
        ],
        "tool_choice": {"type": "auto"}
    }
    
    print("=== 第一回合：傳送 Tool 定義並要求呼叫 (Turn 1: Request Tool Use) ===")
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
    except httpx.ConnectError:
        print("錯誤：無法連線到本機 Proxy，請先確認 Proxy 已經啟動 (例如執行 .\\start_proxy_portal.ps1)")
        return

    if resp.status_code != 200:
        print(f"錯誤：API 回傳狀態碼 {resp.status_code}")
        print("詳細資訊：", resp.text)
        return
        
    data = resp.json()
    print("Proxy 回傳結果：")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    
    content = data.get("content", [])
    tool_use_block = next((item for item in content if item.get("type") == "tool_use"), None)
    
    if not tool_use_block:
        print("\n未偵測到 tool_use 區塊。請確認後端模型支援 Function Calling，且環境變數設定正確。")
        return
        
    print(f"\n成功！偵測到模型要求呼叫工具: {tool_use_block['name']}, ID: {tool_use_block['id']}")
    
    # 第二回合：模擬工具執行完畢，傳回執行結果給模型
    tool_use_id = tool_use_block["id"]
    payload_turn_2 = {
        "model": "haiku",
        "max_tokens": 512,
        "messages": [
            {"role": "user", "content": "用 get_weather 查詢台北的天氣。"},
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "台北天氣晴朗，氣溫 28 度，有微風。"
                    }
                ]
            }
        ],
        "tools": payload["tools"],
        "tool_choice": {"type": "auto"}
    }
    
    print("\n=== 第二回合：傳回工具執行結果 (Turn 2: Send Tool Result) ===")
    resp_2 = httpx.post(url, headers=headers, json=payload_turn_2, timeout=30)
    if resp_2.status_code != 200:
        print(f"錯誤：API 回傳狀態碼 {resp_2.status_code}")
        print("詳細資訊：", resp_2.text)
        return
        
    data_2 = resp_2.json()
    print("Proxy 回傳最終結果：")
    print(json.dumps(data_2, indent=2, ensure_ascii=False))
    
    content_2 = data_2.get("content", [])
    text_block = next((item for item in content_2 if item.get("type") == "text"), None)
    if text_block and "28" in text_block["text"]:
        print("\n[SUCCESS] Test passed! The model successfully parsed tool results and answered.")
    else:
        print("\nTest completed, but the model did not use the tool result.")

if __name__ == "__main__":
    test_tool_call()
