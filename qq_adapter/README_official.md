# QQ_OWARI_BOT 官方 API adapter

此入口使用 QQ Bot 官方 WebSocket Gateway / OpenAPI，不連接 OneBot 或 NapCat。
舊版仍保留在 `qq_bot_legacy.py`。

## 憑證

在專案根目錄 `D:\Adachi-Rei-AI-Bot\.env` 設定：

```env
QQ_OWARI_BOT_ID=QQ 開放平台的 AppID
QQ_OWARI_BOT_SECRET=重設後的 AppSecret
```

可選擇限制處理的群聊，在 `.env` 加入逗號分隔的群 OpenID：

```env
QQ_OWARI_ALLOWED_GROUP_OPENIDS=群 OpenID 1,群 OpenID 2
```

未設定白名單時，程式會處理官方平台推送給機器人的群聊 @ 事件。

群聊指令 `/chat_clean` 會清除觸發該指令的群之短期記憶，不影響長期身份設定或其他群。舊指令 `/forget`、`/clean_message` 和 `/clean_memory` 暫時仍可用。請在 QQ 開放平台將此指令權限設為僅管理員可見／使用。

群聊指令 `/song_show` 會直接列出 `openutau_adapter/songs/` 中所有已收錄的 `.ustx` 曲目，不呼叫 Ollama，也不生成語音。指令與清單會寫入該群短期記憶，讓同一位使用者接著輸入編號或歌名時可以選曲；舊指令 `/songs` 仍可使用。

群聊指令 `/identity_set` 後可輸入最多 300 個字的個人身份介紹，例如 `/identity_set 我是熱音終企劃群的音源管理員`。資料以群 OpenID 和使用者 ID 為鍵，保存在 `data/qq_long_memory.db` 的 SQLite 長期記憶中；重新啟動或清除群短期記憶都不會刪除。使用者在該群發言時，身份資料會作為背景資訊提供給 Ollama；它不會被當成系統指令，也不能覆蓋機器人人格設定。舊指令 `/set_identity` 仍可使用。`/identity_clean` 會清除發令者在當前群的身份，不影響其他群。

一般 @ 訊息一次只處理一則，從圖片處理、Ollama 回覆到語音生成完成都視為忙碌期間。忙碌時，同群訊息會收到「消息处理中，请稍后。」；其他群會收到「正在接收其他群的电波……请稍后再呼叫。」；這些提示不會進入短期記憶。各項斜線指令仍會照常處理。

可使用 `/model_show` 查看目前的 Ollama 對話模型；使用 `/model_set_27B` 或 `/model_set_E4B` 切換到 Gemma 3:27B 或 Gemma 4:E4B。請在 QQ 開放平台依需求將模型切換指令設為「僅管理員可見」；程式不會另外檢查發送者的管理員身分。切換是全域設定，會影響所有群組後續的模型請求。兩個模型都需先存在本機 Ollama 模型庫。舊指令 `/model`、`/set_Gemma3:27B` 和 `/set_Gemma4:E4B` 仍可使用。

群聊 @ 機器人的圖片（包含引用訊息內可取得的圖片）會下載後交給 Ollama 視覺模型分析；每則最多處理 4 張、每張上限 15 MB。GIF 會依播放時間抽取最多 4 張代表畫格並轉成 PNG，提示模型依序比較畫格來描述可見動作；同一則訊息的圖片與 GIF 畫格共用 4 張上限。純圖片訊息會請模型簡短描述圖片。若圖片下載失敗且沒有文字，機器人會提示重新傳送。需要使用支援圖片輸入的 Ollama 模型，並安裝 `qq_adapter/requirements_official.txt` 中的 Pillow。

## 安裝與啟動

在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\qq_adapter\requirements_official.txt
.\.venv\Scripts\python.exe -m qq_adapter.qq_bot_new
```

QQ 開放平台需已設定 WebSocket 事件接收，並把機器人加入可用的測試群。JSON 短期記憶以群 OpenID 分開保存，每群保留最新 100 則；每次啟動仍會清空短期記憶，符合原本 QQ Bot 的行為。測試新版本時不要同時啟動舊版，避免兩個 adapter 共用短期記憶檔。
