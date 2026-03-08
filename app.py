import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import pytz

# --- 設定頁面 (必須在最前面) ---
st.set_page_config(page_title="加密貨幣全能回測系統", layout="wide")

# --- 共用 Session State ---
if 'history_list' not in st.session_state:
    st.session_state.history_list = []
if 'last_result' not in st.session_state:
    st.session_state.last_result = None
if 'default_time_str' not in st.session_state:
    tw_tz = pytz.timezone('Asia/Taipei')
    init_time = datetime.now(tw_tz) - timedelta(days=1)
    st.session_state.default_time_str = init_time.strftime("%Y-%m-%d %H:%M")

# --- 側邊欄：模式選擇 ---
st.sidebar.title("🛠️ 功能選單")
app_mode = st.sidebar.radio("請選擇模式", ["1. 單單回測與紀錄", "2. 資金複利模擬 (Portfolio)"])

# ==========================================
#      模式一：單單回測 (原本的功能)
# ==========================================
if app_mode == "1. 單單回測與紀錄":
    st.sidebar.markdown("---")
    st.sidebar.header("1. 交易參數設定")

    # 0. 交易方向
    direction = st.sidebar.radio("交易方向", ["做多 (Long)", "做空 (Short)"])
    is_long = direction == "做多 (Long)"

    # 1. 幣種選擇
    symbol = st.sidebar.selectbox("交易對", ["BTC/USDT", "ETH/USDT"])

    # 2. 開單時間
    start_time_input = st.sidebar.text_input("開單時間 (YYYY-MM-DD HH:MM)", value=st.session_state.default_time_str)

    # 3. 價格設定
    st.sidebar.subheader("進場與出場")

    if "BTC" in symbol:
        def_min, def_max = 95000.0, 96000.0
        def_sl_long, def_tp1_long, def_tp2_long = 94000.0, 97000.0, 98000.0
        def_sl_short, def_tp1_short, def_tp2_short = 97000.0, 94000.0, 93000.0
    else: # ETH
        def_min, def_max = 2600.0, 2650.0
        def_sl_long, def_tp1_long, def_tp2_long = 2550.0, 2700.0, 2750.0
        def_sl_short, def_tp1_short, def_tp2_short = 2750.0, 2600.0, 2550.0

    entry_min = st.sidebar.number_input("進場下限", value=def_min, step=10.0)
    entry_max = st.sidebar.number_input("進場上限", value=def_max, step=10.0)

    use_tp2 = st.sidebar.checkbox("啟用分批止盈 (TP1 & TP2)", value=True)

    if is_long:
        sl_val = def_sl_long
        tp1_val = def_tp1_long
        tp2_val = def_tp2_long
    else:
        sl_val = def_sl_short
        tp1_val = def_tp1_short
        tp2_val = def_tp2_short

    sl_price = st.sidebar.number_input("原始止損 (SL)", value=sl_val, step=10.0)

    if use_tp2:
        tp1_price = st.sidebar.number_input("第一止盈 (TP1 - 平半倉+移止損)", value=tp1_val, step=10.0)
        tp2_price = st.sidebar.number_input("第二止盈 (TP2 - 最終止盈)", value=tp2_val, step=10.0)
    else:
        tp1_price = st.sidebar.number_input("止盈價格 (TP)", value=tp1_val, step=10.0)
        tp2_price = None 

    # 4. 資金管理
    st.sidebar.subheader("資金管理")
    capital = st.sidebar.number_input("單筆本金 (USDT)", value=1000.0)
    leverage = st.sidebar.number_input("槓桿倍數", value=10, min_value=1, max_value=125)
    
    # 5. 時間框架選擇
    st.sidebar.subheader("回測設定")
    timeframe_options = {
        "1分鐘": "1m",
        "5分鐘": "5m", 
        "15分鐘": "15m",
        "1小時": "1h"
    }
    timeframe_display = st.sidebar.selectbox(
        "K線週期", 
        options=list(timeframe_options.keys()),
        index=2,  # 預設15分鐘
        help="較大的週期可減少數據量，提高回測速度。建議：短期持倉用1-5分鐘，長期持倉用15分鐘-1小時"
    )
    timeframe = timeframe_options[timeframe_display]
    
    st.sidebar.info("💡 系統會自動獲取數據直到倉位完全結清")

    # --- 匯入功能 ---
    st.sidebar.markdown("---")
    st.sidebar.header("📂 資料管理")
    uploaded_file = st.sidebar.file_uploader("匯入舊紀錄 (CSV)", type=['csv'], key="import_csv")
    if uploaded_file is not None:
        if st.sidebar.button("📥 確認匯入資料"):
            try:
                imported_df = pd.read_csv(uploaded_file)
                imported_list = imported_df.to_dict('records')
                st.session_state.history_list.extend(imported_list)
                st.sidebar.success(f"成功匯入 {len(imported_list)} 筆交易！")
            except Exception as e:
                st.sidebar.error(f"匯入失敗: {e}")

    # --- 核心函數 (模式一) ---
    def fetch_and_backtest_auto(symbol, start_str, e_min, e_max, sl, tp1, tp2, use_tp2, capital, leverage, is_long, timeframe='15m'):
        """
        自動獲取數據並回測，直到倉位完全結清
        採用邊獲取邊回測的策略，當倉位結清時自動停止
        """
        exchange = ccxt.binance()
        tw_tz = pytz.timezone('Asia/Taipei')
        
        # 解析開始時間
        try:
            dt_obj = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
            local_dt = tw_tz.localize(dt_obj)
            utc_dt = local_dt.astimezone(pytz.utc)
            since_timestamp = int(utc_dt.timestamp() * 1000)
        except ValueError:
            st.error("時間格式錯誤")
            return None, None
        
        # 回測狀態變數
        state = "等待進場"
        entry_price = 0
        entry_time = None
        exit_log = []
        current_sl = sl
        
        # 根據時間框架調整批次設定
        timeframe_config = {
            '1m': {'batch_limit': 1440, 'max_batches': 365, 'candles_per_day': 1440},
            '5m': {'batch_limit': 1440, 'max_batches': 365, 'candles_per_day': 288},
            '15m': {'batch_limit': 1440, 'max_batches': 365, 'candles_per_day': 96},
            '1h': {'batch_limit': 1000, 'max_batches': 365, 'candles_per_day': 24}
        }
        
        config = timeframe_config.get(timeframe, timeframe_config['15m'])
        batch_limit = config['batch_limit']
        max_batches = config['max_batches']
        candles_per_day = config['candles_per_day']
        
        # 數據獲取變數
        all_ohlcv = []
        current_since = since_timestamp
        batch_count = 0
        
        progress_placeholder = st.empty()
        
        try:
            while batch_count < max_batches:
                batch_count += 1
                
                # 獲取一批數據
                progress_placeholder.info(f"📊 正在獲取第 {batch_count} 批數據（{timeframe}）...")
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=current_since, limit=batch_limit)
                
                if not ohlcv:
                    progress_placeholder.warning("⚠️ 無法獲取更多數據")
                    break
                
                all_ohlcv.extend(ohlcv)
                
                # 將當前批次轉換為 DataFrame 進行回測
                df_batch = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df_batch['timestamp'] = pd.to_datetime(df_batch['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei')
                
                # 對當前批次進行回測
                for index, row in df_batch.iterrows():
                    current_low = row['low']
                    current_high = row['high']
                    current_time = row['timestamp']
                    
                    if state == "等待進場":
                        if current_low <= e_max and current_high >= e_min:
                            state = "持倉_全倉"
                            entry_time = current_time
                            entry_price = (e_min + e_max) / 2
                    
                    elif state == "持倉_全倉":
                        if is_long:
                            if current_low <= current_sl:
                                state = "已出場"
                                exit_log.append((current_time, current_sl, 1.0, "止損 (SL)"))
                                break
                            elif current_high >= tp1:
                                if use_tp2:
                                    state = "持倉_半倉"
                                    exit_log.append((current_time, tp1, 0.5, "TP1 (平半倉)"))
                                    current_sl = entry_price
                                else:
                                    state = "已出場"
                                    exit_log.append((current_time, tp1, 1.0, "止盈 (TP)"))
                                    break
                        else:  # Short
                            if current_high >= current_sl:
                                state = "已出場"
                                exit_log.append((current_time, current_sl, 1.0, "止損 (SL)"))
                                break
                            elif current_low <= tp1:
                                if use_tp2:
                                    state = "持倉_半倉"
                                    exit_log.append((current_time, tp1, 0.5, "TP1 (平半倉)"))
                                    current_sl = entry_price
                                else:
                                    state = "已出場"
                                    exit_log.append((current_time, tp1, 1.0, "止盈 (TP)"))
                                    break
                    
                    elif state == "持倉_半倉":
                        if is_long:
                            if current_low <= current_sl:
                                state = "已出場"
                                exit_log.append((current_time, current_sl, 0.5, "保本出場 (BE)"))
                                break
                            elif current_high >= tp2:
                                state = "已出場"
                                exit_log.append((current_time, tp2, 0.5, "TP2 (完結)"))
                                break
                        else:  # Short
                            if current_high >= current_sl:
                                state = "已出場"
                                exit_log.append((current_time, current_sl, 0.5, "保本出場 (BE)"))
                                break
                            elif current_low <= tp2:
                                state = "已出場"
                                exit_log.append((current_time, tp2, 0.5, "TP2 (完結)"))
                                break
                
                # 檢查是否已經結清倉位
                if state == "已出場":
                    days_approx = len(all_ohlcv) / candles_per_day
                    progress_placeholder.success(f"✅ 倉位已結清！共獲取 {len(all_ohlcv)} 根K線（約 {days_approx:.1f} 天）")
                    break
                
                # 更新下一次獲取的起始時間
                current_since = ohlcv[-1][0] + 60000
                
                # 如果獲取的數據少於請求的數量，說明已經到最新數據了
                if len(ohlcv) < batch_limit:
                    last_data_time = df_batch.iloc[-1]['timestamp'].strftime("%Y-%m-%d %H:%M")
                    days_approx = len(all_ohlcv) / candles_per_day
                    progress_placeholder.warning(f"⚠️ 已獲取到交易所最新數據（共 {len(all_ohlcv)} 根K線，約 {days_approx:.1f} 天）\n最新數據時間：{last_data_time}\n倉位仍未結清，可能需要等待更多時間或調整止盈止損價格")
                    break
            
            # 如果達到最大批次限制
            if batch_count >= max_batches and state != "已出場":
                progress_placeholder.warning(f"⚠️ 已達到最大獲取限制（{max_batches} 批），倉位仍未結清\n建議檢查止盈止損設定是否合理")
            
            # 構建完整的 DataFrame
            if not all_ohlcv:
                return None, None
            
            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei')
            df = df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
            
            return df, (state, entry_time, entry_price, exit_log)
            
        except Exception as e:
            st.error(f"回測過程發生錯誤: {e}")
            return None, None

    # --- 主畫面顯示 (模式一) ---
    st.title("📈 模式一：單單回測與紀錄")

    if st.sidebar.button("🚀 開始回測"):
        df, backtest_result = fetch_and_backtest_auto(
            symbol, start_time_input, entry_min, entry_max, 
            sl_price, tp1_price, tp2_price, use_tp2, capital, leverage, is_long, timeframe
        )
        
        if df is not None and backtest_result is not None:
            final_status, e_time, e_price, exits = backtest_result
            
            total_pnl = 0
            exit_details_str = ""
            
            if final_status == "等待進場":
                st.warning("⚠️ 未進場")
            elif not exits: 
                # 持倉中但未結算（已獲取到最新數據或達到上限）
                if e_time:
                    e_time_str = e_time.strftime("%Y-%m-%d %H:%M")
                    last_data_time = df.iloc[-1]['timestamp'].strftime("%Y-%m-%d %H:%M")
                    st.warning(f"🔵 持倉中（未結算）")
                    st.info(f"""
                    **持倉資訊：**
                    - 進場時間：{e_time_str}
                    - 最新數據時間：{last_data_time}
                    - 當前狀態：{final_status}
                    
                    ⚠️ **倉位在可獲取的數據範圍內未觸及止盈或止損**
                    可能原因：已獲取到交易所最新數據，或倉位持續時間超過90天
                    """)
                else:
                    st.info("🔵 持倉中")
            else:
                if e_time:
                    e_time_str = e_time.strftime("%m/%d %H:%M")
                    exit_details_str += f"[進場: {e_time_str}] "

                full_size = (capital * leverage) / e_price
                
                for x_time, x_price, x_ratio, x_type in exits:
                    part_size = full_size * x_ratio
                    if is_long:
                        part_pnl = (x_price - e_price) * part_size
                    else:
                        part_pnl = (e_price - x_price) * part_size
                    
                    total_pnl += part_pnl
                    time_str = x_time.strftime("%m/%d %H:%M")
                    exit_details_str += f"[{time_str} | {x_type}: {x_price}] "
                
                pnl_percent = (total_pnl / capital) * 100
                
                color = "green" if total_pnl > 0 else "red"
                st.markdown(f"### 總損益: :{color}[{total_pnl:.2f} U] ({pnl_percent:.2f}%)")
                st.write(f"交易明細: {exit_details_str}")

                st.session_state.last_result = {
                    "紀錄時間": datetime.now(pytz.timezone('Asia/Taipei')).strftime("%H:%M:%S"),
                    "幣種": symbol,
                    "方向": "做多" if is_long else "做空",
                    "損益(U)": round(total_pnl, 2),
                    "報酬率(%)": round(pnl_percent, 2),
                    "交易明細": exit_details_str, 
                    "本金": capital,
                    "進場價": round(e_price, 2),
                    "開單時間": start_time_input
                }

            fig = go.Figure()
            fig.add_trace(go.Candlestick(x=df['timestamp'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='Price'))
            fig.add_hrect(y0=entry_min, y1=entry_max, fillcolor="blue", opacity=0.1)
            fig.add_hline(y=sl_price, line_dash="dash", line_color="red", annotation_text="SL")
            fig.add_hline(y=tp1_price, line_dash="dash", line_color="green", annotation_text="TP1")
            if use_tp2:
                fig.add_hline(y=tp2_price, line_dash="dash", line_color="green", annotation_text="TP2")
            if e_time:
                fig.add_annotation(x=e_time, y=e_price, text="Open", showarrow=True, arrowhead=1, arrowcolor="blue")
            for x_time, x_price, _, x_type in exits:
                col = "green" if "TP" in x_type else "red"
                if "保本" in x_type: col = "gray"
                fig.add_annotation(x=x_time, y=x_price, text=x_type, showarrow=True, arrowhead=1, arrowcolor=col)
            fig.update_layout(height=500, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)

    if st.session_state.last_result is not None:
        st.write("---")
        if st.button("💾 加入紀錄列表"):
            st.session_state.history_list.append(st.session_state.last_result)
            st.success("已加入！")

    if len(st.session_state.history_list) > 0:
        st.write("### 📝 回測紀錄表")
        history_df = pd.DataFrame(st.session_state.history_list)
        st.dataframe(history_df, use_container_width=True)
        csv = history_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載紀錄 (CSV)", csv, 'backtest.csv', 'text/csv')

# ==========================================
#      模式二：資金複利模擬 (高水位鎖定版)
# ==========================================
elif app_mode == "2. 資金複利模擬 (Portfolio)":
    st.sidebar.markdown("---")
    st.sidebar.header("2. 複利參數設定")
    
    # 輸入 CSV
    uploaded_file_m2 = st.sidebar.file_uploader("上傳回測紀錄 CSV", type=['csv'], key="portfolio_csv")
    
    # 資金設定
    initial_capital = st.sidebar.number_input("初始總資金 (Total Equity)", value=10000.0)
    risk_percent = st.sidebar.slider("單筆倉位佔比 (%)", 1, 100, 5)
    
    # --- 新增：高水位鎖定模式開關 ---
    use_hwm_mode = st.sidebar.checkbox("🔥 啟用「高水位鎖定法」 (High Water Mark)", value=False)
    if use_hwm_mode:
        st.sidebar.info("💡 說明：\n贏了 -> 本金變大，下注變大 (複利)。\n輸了 -> 下注金額「維持」在歷史最高本金計算的金額 (不縮手)。")
    
    # --- 新增：手續費計算 ---
    st.sidebar.markdown("---")
    use_fee = st.sidebar.checkbox("💰 啟用手續費計算", value=False)
    if use_fee:
        fee_percent = st.sidebar.number_input("手續費百分比 (%)", min_value=0.0, max_value=100.0, value=10.0, step=1.0, 
                                              help="從每筆盈利交易中扣除的手續費百分比，例如 10% 表示盈利的十分之一")
        st.sidebar.info(f"💡 每筆盈利交易將扣除 {fee_percent}% 作為手續費")
    else:
        fee_percent = 0.0
    
    st.title("📊 模式二：資金複利模擬")
    st.write(f"當前模式：{'**🔥 高水位鎖定 (High Water Mark)**' if use_hwm_mode else '**🛡️ 標準複利 (Standard Compounding)**'}")
    st.info("💡 **計算說明**：本模式僅使用 CSV 中的「報酬率(%)」進行複利計算，不參考「損益(U)」等絕對金額欄位。")
    
    if uploaded_file_m2 is not None:
        try:
            # 1. 讀取數據
            df_port = pd.read_csv(uploaded_file_m2)
            
            # 檢查必要欄位
            required_cols = ['報酬率(%)', '開單時間']
            if not all(col in df_port.columns for col in required_cols):
                st.error(f"CSV 格式錯誤！缺少必要欄位: {required_cols}")
            else:
                # 顯示CSV資訊
                st.success(f"✅ 成功讀取 {len(df_port)} 筆交易紀錄")
                with st.expander("📄 CSV 欄位資訊"):
                    st.write(f"**包含欄位：** {', '.join(df_port.columns.tolist())}")
                    st.write(f"**用於計算：** 報酬率(%) ← 僅使用此欄位")
                    if '損益(U)' in df_port.columns:
                        st.write(f"**忽略欄位：** 損益(U), 本金 等絕對金額欄位")
                
                # 2. 數據預處理
                try:
                    df_port['開單時間'] = pd.to_datetime(df_port['開單時間'])
                except:
                    st.warning("時間格式解析可能有誤，將依照 CSV 原始順序計算")
                
                df_port = df_port.sort_values(by='開單時間').reset_index(drop=True)
                
                # 3. 複利計算核心
                equity = [initial_capital]
                pnl_history = []
                drawdown_history = []
                
                df_port['交易前本金'] = 0.0
                df_port['計算基準(HWM)'] = 0.0 # 顯示用
                df_port['下注金額'] = 0.0
                df_port['本筆損益'] = 0.0
                df_port['手續費'] = 0.0  # 新增手續費欄位
                df_port['淨損益'] = 0.0  # 新增淨損益欄位（扣除手續費後）
                df_port['交易後本金'] = 0.0
                
                current_equity = initial_capital
                peak_equity = initial_capital # 用來計算最大回撤
                
                # 高水位紀錄 (High Water Mark) - 專門用來計算下注金額
                hwm_for_betting = initial_capital 
                
                # 手續費統計
                total_fees = 0.0
                
                for index, row in df_port.iterrows():
                    # 取得單筆 ROI
                    original_roi = row['報酬率(%)'] / 100.0
                    
                    # 應用手續費（所有交易都扣除手續費）
                    if original_roi > 0:
                        # 盈利交易：扣除盈利的手續費百分比
                        # 例如：盈利5%，手續費10%，則實際ROI = 5% × (1 - 10%) = 4.5%
                        adjusted_roi = original_roi * (1 - fee_percent / 100.0)
                        fee_roi = original_roi - adjusted_roi  # 手續費對應的ROI
                    else:
                        # 虧損交易：虧損本身 + 額外扣除虧損金額的手續費
                        # 例如：虧損-5%，手續費10%，則實際ROI = -5% + (-5% × 10%) = -5.5%
                        fee_roi = abs(original_roi) * (fee_percent / 100.0)  # 手續費（正數）
                        adjusted_roi = original_roi - fee_roi  # 虧損更大
                    
                    # --- 核心邏輯分支 ---
                    if use_hwm_mode:
                        # 高水位模式：下注金額 = 歷史最高本金 * 百分比
                        # 如果賺錢，current_equity 變高，hwm_for_betting 也會變高 (複利)
                        # 如果賠錢，current_equity 變低，但 hwm_for_betting 保持不變 (鎖定火力)
                        
                        # 先更新 HWM (如果是第一筆或剛創新高)
                        if current_equity > hwm_for_betting:
                            hwm_for_betting = current_equity
                            
                        base_capital = hwm_for_betting
                    else:
                        # 標準模式：下注金額 = 當前本金 * 百分比
                        # 賠錢了，本金變少，下注金額就自動變少 (防守)
                        base_capital = current_equity

                    # 計算下注金額
                    position_size = base_capital * (risk_percent / 100.0)
                    
                    # 使用調整後的ROI計算損益
                    trade_pnl = position_size * original_roi  # 原始損益
                    fee = position_size * fee_roi  # 手續費金額
                    net_pnl = position_size * adjusted_roi  # 淨損益
                    
                    total_fees += fee
                    
                    # 更新本金（使用淨損益）
                    prev_equity = current_equity
                    current_equity += net_pnl
                    
                    # 紀錄數據
                    equity.append(current_equity)
                    pnl_history.append(net_pnl)  # 使用淨損益
                    
                    # 計算回撤
                    peak_equity = max(peak_equity, current_equity)
                    dd = (current_equity - peak_equity) / peak_equity * 100
                    drawdown_history.append(dd)
                    
                    # 更新 DataFrame
                    df_port.at[index, '交易前本金'] = round(prev_equity, 2)
                    df_port.at[index, '計算基準(HWM)'] = round(base_capital, 2) if use_hwm_mode else round(prev_equity, 2)
                    df_port.at[index, '下注金額'] = round(position_size, 2)
                    df_port.at[index, '本筆損益'] = round(trade_pnl, 2)
                    df_port.at[index, '手續費'] = round(fee, 2)
                    df_port.at[index, '淨損益'] = round(net_pnl, 2)
                    df_port.at[index, '交易後本金'] = round(current_equity, 2)

                # 4. 顯示統計數據
                total_return = (current_equity - initial_capital)
                total_return_pct = (total_return / initial_capital) * 100
                max_dd = min(drawdown_history) if drawdown_history else 0
                win_rate = len(df_port[df_port['淨損益'] > 0]) / len(df_port) * 100  # 使用淨損益計算勝率
                
                st.markdown("### 📊 模擬結果總覽")
                
                if use_fee:
                    # 啟用手續費時顯示5欄
                    col1, col2, col3, col4, col5 = st.columns(5)
                    col1.metric("最終總資金", f"{current_equity:,.2f} U")
                    col2.metric("總報酬率", f"{total_return_pct:.2f} %", delta=f"{total_return:,.2f} U")
                    col3.metric("總手續費", f"{total_fees:,.2f} U", delta_color="inverse")
                    col4.metric("最大回撤 (MDD)", f"{max_dd:.2f} %", delta_color="inverse")
                    col5.metric("勝率", f"{win_rate:.1f} %")
                else:
                    # 未啟用手續費時顯示4欄
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("最終總資金", f"{current_equity:,.2f} U")
                    col2.metric("總報酬率", f"{total_return_pct:.2f} %", delta=f"{total_return:,.2f} U")
                    col3.metric("最大回撤 (MDD)", f"{max_dd:.2f} %", delta_color="inverse")
                    col4.metric("勝率", f"{win_rate:.1f} %")
                
                # 5. 繪製資金曲線
                st.markdown("### 📈 資金成長曲線 (Equity Curve)")
                chart_data = pd.DataFrame({
                    'Trade Count': range(len(equity)),
                    'Equity': equity
                })
                fig = px.line(chart_data, x='Trade Count', y='Equity', title='資金累積走勢')
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)
                
                # 6. 顯示詳細計算表
                st.markdown("### 📋 逐筆計算明細")
                
                # 根據不同模式組合顯示不同欄位
                if use_fee:
                    if use_hwm_mode:
                        cols_to_show = ['開單時間', '報酬率(%)', '計算基準(HWM)', '下注金額', '本筆損益', '手續費', '淨損益', '交易後本金']
                    else:
                        cols_to_show = ['開單時間', '報酬率(%)', '交易前本金', '下注金額', '本筆損益', '手續費', '淨損益', '交易後本金']
                else:
                    if use_hwm_mode:
                        cols_to_show = ['開單時間', '報酬率(%)', '計算基準(HWM)', '下注金額', '本筆損益', '交易後本金']
                    else:
                        cols_to_show = ['開單時間', '報酬率(%)', '交易前本金', '下注金額', '本筆損益', '交易後本金']
                
                st.dataframe(df_port[cols_to_show], use_container_width=True)
                
        except Exception as e:
            st.error(f"運算發生錯誤: {e}")
    else:
        st.info("👈 請從左側上傳「模式一」產生的 CSV 檔案以開始模擬。")