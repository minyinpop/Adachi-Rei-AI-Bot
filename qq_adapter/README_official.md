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

未設定白名單時，程式會處理官方平台推送給機器人的群聊訊息中 @ 機器人的那些。

## 群設定：接收群內全部訊息

每個要使用的群都必須由群主在群機器人設定中，把訊息接收範圍設為「接收群內全部訊息」。此模式下 QQ 以 `GROUP_MESSAGE_CREATE` 推送每則群訊息，且只有這種事件會附帶被 @ 成員的 `mentions` 資料；僅接收 @ 訊息時的 `GROUP_AT_MESSAGE_CREATE` 會把訊息裡所有 @ 都刪掉，Bot 無從得知被 @ 的是誰，因此程式不再處理該事件。

收到全部訊息後，程式只回應 `mentions` 中有 `is_you: true`（或內容含本機器人 AppID 標記）的訊息。沒有 @ 機器人的群聊不會回覆，但會以 `group_chat` 類型寫入該群短期記憶（@ 會換成可讀名稱、圖片記為「[图片]」），讓群友問「剛剛某人說的如何」時，模型能依據前文回答；這些訊息在提示詞中會標示「群聊，未@热音终」，只作背景參考。選歌等依賴「上一輪與機器人對話」的流程會略過這類訊息，所以中間有人插話也不影響回編號選歌。`qqbot-agent-sdk` 1.2.2 預設會丟棄 `GROUP_MESSAGE_CREATE`，`official_bot/sdk.py` 會在載入時把它註冊回 SDK 的分派與解析流程。

群聊指令 `/chat_clean` 會清除觸發該指令的群之短期記憶，不影響長期身份設定或其他群。舊指令 `/forget`、`/clean_message` 和 `/clean_memory` 暫時仍可用。請在 QQ 開放平台將此指令權限設為僅管理員可見／使用。

群聊指令 `/song_show` 會直接列出 `openutau_adapter/songs/` 中所有已收錄的 `.ustx` 曲目，不呼叫 Ollama，也不生成語音。指令與清單會寫入該群短期記憶，讓同一位使用者接著輸入編號或歌名時可以選曲；舊指令 `/songs` 仍可使用。

群聊指令 `/identity_set` 後可輸入最多 500 個字的個人身份介紹，例如 `/identity_set 我是熱音終企劃群的音源管理員`。身份只能透過指令設定或清除，一般聊天中的自我介紹不會被自動寫入，避免模型誤判而覆蓋使用者手動設定的內容。資料以群 OpenID 和使用者 ID 為鍵，保存在 `data/qq_long_memory.db` 的 SQLite 長期記憶中；重新啟動或清除群短期記憶都不會刪除。使用者在該群發言時，身份資料會作為背景資訊提供給 Ollama；它不會被當成系統指令，也不能覆蓋機器人人格設定。`/identity_show` 會顯示發令者在當前群保存的身份；`/identity_clean` 會清除該身份，不影響其他群。舊指令 `/set_identity` 仍可使用。

對話中被 @ 的群成員會優先從 QQ 原始事件的 `mentions` 欄位解析（取 `member_openid` 與 `username`）；若該欄位缺失，會再從訊息中的 `<@ID>`／`<qqbot-at-user>` 標記擷取成員 OpenID。Bot 會用該 ID 查詢成員在當前群的身份設定，並把可讀的 @ 名稱與身份資料提供給 Ollama。訊息中 @ 機器人本身的標記不算被 @ 群友：在句首會被移除，讓斜線指令能正常比對；在句中會換成「@热音终」。未設定身份或事件中沒有可解析的成員 ID 時，模型應說沒有相關資料，不依暱稱猜測。

每則群訊息（不論是否 @ 機器人）都會把發言者與被 @ 成員的最新顯示名稱寫入 `data/qq_long_memory.db` 的 `group_members` 群成員名冊，重啟後仍保留，讓機器人認得還沒在本次啟動後發言的群友。回覆時，程式會從名冊與身份表挑出「相關群友」一併交給 Ollama：訊息中出現某位群友的名稱，或訊息與某人身份內容共用關鍵詞（例如問「博士」會帶入身份寫著「可以叫我博士」的成員）。比對前會用 OpenCC（`opencc-python-reimplemented`）把繁體轉成簡體，繁簡混用也能對上（例如問「掃地姬」能找到身份寫「扫地姬」的成員）；一般常用詞不列入比對，未設定身份的成員不會被帶入，每次最多 5 位；發問者本人與被 @ 成員的身份照舊另外提供。

機器人也能在回覆中真的 @ 群友：模型只在被明確要求時於 `reply_text` 寫「@成員名稱」，程式再依群成員名冊、本輪被 @ 的成員、發言者與短期記憶中的發言者建立「名稱 → OpenID」對照，把認得的 `@名稱` 換成 `<qqbot-at-user id="OpenID" />`，並改以 Markdown 訊息送出（一般文字訊息中的這個標記不會生效，只會顯示原文）。名稱比對同樣接受繁體與簡體寫法。沒有要 @ 人時仍送一般文字；名稱對不到、或同名對應到多位成員時保留純文字，不會誤 @。短期記憶中保存的是原本的「@名稱」文字。

每則回覆等待 Ollama 最多 240 秒（`response_pipeline.py` 的 `OLLAMA_REPLY_TIMEOUT_SECONDS`），首次請求、JSON 重試與純文字備援共用這個上限。QQ 只接受訊息 5 分鐘內的被動回覆，因此超時後會中斷連線（Ollama 隨之停止生成、釋放 GPU），回覆「这次思考太久了，热音终先停下来。请换个说法再问一次。」並解除忙碌狀態，避免模型陷入過長思考而卡住整個 Bot。

一般 @ 訊息一次只處理一則，從圖片處理、Ollama 回覆到語音生成完成都視為忙碌期間。忙碌時，同群訊息會收到「消息处理中，请稍后。」；其他群會收到「正在接收其他群的电波……请稍后再呼叫。」；這些提示不會進入短期記憶。各項斜線指令仍會照常處理。

可使用 `/model_show` 查看目前的 Ollama 對話模型；使用 `/model_set_27B3` 切換到 `gemma3:27b`，或使用 `/model_set_26B4` 切換到 `gemma4:26b`。請在 QQ 開放平台依需求將模型切換指令設為「僅管理員可見」；程式不會另外檢查發送者的管理員身分。切換是全域設定，會影響所有群組後續的模型請求。兩個模型都需先存在本機 Ollama 模型庫。

群聊 @ 機器人的圖片（包含引用訊息內可取得的圖片）會下載後交給 Ollama 視覺模型分析；每則最多處理 4 張、每張上限 15 MB。GIF 會依播放時間抽取最多 4 張代表畫格並轉成 PNG，提示模型依序比較畫格來描述可見動作；同一則訊息的圖片與 GIF 畫格共用 4 張上限。純圖片訊息會請模型簡短描述圖片。QQ 收藏／自訂貼圖（`faceType=6`）會以圖片附件送達，因此和一般圖片一樣交給模型辨識，訊息中的貼圖標記則直接移除；QQ 系統小黃臉表情（`faceType=1` 等）沒有圖片，會依標記內 `ext` 的名稱轉成「[表情：敲打]」這類文字，沒有名稱時記為「[表情]」。若圖片下載失敗且沒有文字，機器人會提示重新傳送。需要使用支援圖片輸入的 Ollama 模型，並安裝 `qq_adapter/requirements_official.txt` 中的 Pillow。

## 完整演唱

唱歌預設只截取約 10 秒的精華片段。訊息中出現「整首」「全曲」「全首」「完整」「唱完」等字眼時（`song_intent.is_full_song_request`），會改為不裁切、渲染整首 USTX：先回覆「正在准备完整演唱：歌名，大约需要 1～2 分钟。」，渲染完成後再送語音與「正在播放：歌名」。最長約 4 分鐘的歌曲渲染約需 1 分鐘、WAV 約 22 MB。若 QQ 因長度或大小拒收語音，文字會改為說明完整版語音沒能發送。

## 每日排程訊息

Bot 可在每天固定時間主動發訊息到指定群，設定放在 `qq_adapter/settings/scheduled_messages.json`：

```json
[
  {
    "time": "15:00",
    "group_openid": "群 OpenID",
    "text": "オワリ提醒群ユ们，3点了，该喝茶了",
    "image": "images/tea_time.jpg"
  },
  {
    "time": "19:00",
    "group_openid": "群 OpenID",
    "text": "晚上好，没吃饭的群友们该吃饭了"
  }
]
```

- `time`：電腦本地時間，24 小時制 `HH:MM`。
- `group_openid`：目標群的 OpenID，可從 Bot 收到該群訊息時的 log 取得。
- `date`：選填，格式 `YYYY-MM-DD`。填了就只在那天的 `time` 發送一次（例如節日問候），時間過了自動不再發；不填則每天重複。
- `text`／`image`：至少填一項。`image` 為相對於 `qq_adapter/settings/` 的路徑，圖片放在 `settings/images/`。
- 面向 QQ 的文字請用簡體中文。

有圖片時，文字與圖片會合併成一則富媒體訊息送出，只占用一次主動訊息額度；QQ 固定把圖片顯示在上、文字在下，一般訊息格式無法調換。需要文字在上時只能拆成兩則（占兩次額度），或改用 Markdown 訊息（需另外申請權限並把圖片放在公開網址）。

設定只在啟動時讀取，修改後要重啟 Bot 才會生效。設定檔語法錯誤時只會在 log 印出錯誤並停用排程；單筆格式錯誤、缺欄位或找不到圖片時只略過該筆，都不影響 Bot 回應 @ 訊息。排程只在 Bot 執行期間有效：電腦睡眠、休眠或關機期間錯過的時間點不會補發。QQ 開放平台需開啟機器人主動發言權限，且群主動訊息有次數限制；發送結果會以 `【⏰】`／`【❌】` 記錄在 log。排程不受 `QQ_OWARI_ALLOWED_GROUP_OPENIDS` 白名單限制。

## 安裝與啟動

在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\qq_adapter\requirements_official.txt
.\.venv\Scripts\python.exe -m qq_adapter.qq_bot_new
```

官方 Bot 的程式已拆分在 `qq_adapter/official_bot/`：`runtime.py` 管理啟動與 WebSocket，`bot.py` 接收並分派 QQ 事件，`commands.py` 處理斜線指令，`response_pipeline.py` 負責 Ollama、OpenUTAU 與回覆流程，`message_utils.py`／`media.py` 處理訊息與圖片，`settings.py`／`sdk.py` 集中設定與 SDK 載入，`base.py` 提供共用的被動回覆 helper，`scheduler.py` 負責每日排程訊息。`qq_bot_new.py` 保留為啟動入口。唱歌意圖判斷與曲目清單格式放在 `qq_adapter/song_intent.py`，由官方 Bot 與舊版共用，官方 Bot 不再依賴 `qq_bot_legacy.py`。

QQ 開放平台需已設定 WebSocket 事件接收，並把機器人加入可用的測試群。JSON 短期記憶以群 OpenID 分開保存，每群保留最新 100 則（含未 @ 的群聊），每次回覆時提供最近 32 則給 Ollama；每次啟動仍會清空短期記憶，符合原本 QQ Bot 的行為。同一時間只能執行一個 Bot（包括不要同時啟動舊版），否則每則 @ 訊息會被回覆兩次，排程也會重複發送，兩個程序還會互相覆蓋並清空短期記憶檔。
