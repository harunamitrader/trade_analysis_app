import streamlit as st
import pandas as pd
import io
from datetime import timedelta

def get_adjusted_date(dt):
    if dt.hour < 7:
        return dt - pd.Timedelta(days=1)
    return dt

def process_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    取引履歴データを処理し、各決済取引に保有時間、ロット数、取引種別、決済年月を付与する。
    """
    df['約定日時'] = pd.to_datetime(df['約定日時'])
    for col in ['約定数量', '約定単価', '建単価', '実現損益（円貨）']:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    new_trades = df[df['取引区分'].str.contains('新規', na=False)].copy()
    closed_trades = df[df['取引区分'].str.contains('決済|ロスカット', na=False)].copy()

    if closed_trades.empty:
        return pd.DataFrame()

    new_trades['残り数量'] = new_trades['約定数量']
    new_trades = new_trades.sort_values(by='約定日時').reset_index(drop=True)

    results = []

    for _, closed_trade in closed_trades.sort_values(by='約定日時').iterrows():
        adjusted_date = get_adjusted_date(closed_trade['約定日時'])
        closed_qty = closed_trade['約定数量']
        is_matched = False
        
        potential_matches = new_trades[
            (new_trades['銘柄名'] == closed_trade['銘柄名']) &
            (new_trades['約定単価'] == closed_trade['建単価']) &
            (new_trades['残り数量'] > 0) &
            (new_trades['約定日時'] < closed_trade['約定日時'])
        ]

        for match_index, new_trade in potential_matches.iterrows():
            if closed_qty <= 0:
                break
            
            is_matched = True
            matched_qty = min(closed_qty, new_trade['残り数量'])
            
            is_fx = 'JPY' in closed_trade['銘柄名'] or 'USD' in closed_trade['銘柄名'] or 'EUR' in closed_trade['銘柄名']
            lot_size = 10000 if is_fx else 1
            normalized_lot = matched_qty / lot_size
            trade_type = 'FX' if is_fx else 'CFD'

            holding_time = closed_trade['約定日時'] - new_trade['約定日時']
            position_type = 'ロング' if closed_trade['売買区分'] == '売' else 'ショート'
            pro_rata_profit = (closed_trade['実現損益（円貨）'] / closed_trade['約定数量']) * matched_qty if closed_trade['約定数量'] != 0 else 0

            results.append({
                '銘柄名': closed_trade['銘柄名'],
                'ポジション': position_type,
                '実現損益（円貨）': pro_rata_profit,
                '保有時間': holding_time,
                'ロット数': normalized_lot,
                '取引種別': trade_type,
                '決済年月': adjusted_date.strftime('%Y-%m'),
                '決済日': adjusted_date.strftime('%Y-%m-%d')
            })

            new_trades.at[match_index, '残り数量'] -= matched_qty
            closed_qty -= matched_qty

        if not is_matched:
            is_fx = 'JPY' in closed_trade['銘柄名'] or 'USD' in closed_trade['銘柄名'] or 'EUR' in closed_trade['銘柄名']
            lot_size = 10000 if is_fx else 1
            normalized_lot = closed_trade['約定数量'] / lot_size
            trade_type = 'FX' if is_fx else 'CFD'
            position_type = 'ロング' if closed_trade['売買区分'] == '売' else 'ショート'

            results.append({
                '銘柄名': closed_trade['銘柄名'],
                'ポジション': position_type,
                '実現損益（円貨）': closed_trade['実現損益（円貨）'],
                '保有時間': pd.NaT,
                'ロット数': normalized_lot,
                '取引種別': trade_type,
                '決済年月': adjusted_date.strftime('%Y-%m'),
                '決済日': adjusted_date.strftime('%Y-%m-%d')
            })

    return pd.DataFrame(results)

def analyze_summary(df: pd.DataFrame, group_by_cols: list) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    df['実現損益（円貨）'] = pd.to_numeric(df['実現損益（円貨）'], errors='coerce').fillna(0)
    df['1ロットあたり損益'] = (df['実現損益（円貨）'] / df['ロット数']).replace([float('inf'), -float('inf')], 0).fillna(0)

    summary = df.groupby(group_by_cols).apply(lambda g: pd.Series({
        '総損益': g['実現損益（円貨）'].sum(),
        '取引回数': len(g),
        '勝ち数': (g['実現損益（円貨）'] > 0).sum(),
        '負け数': (g['実現損益（円貨）'] < 0).sum(),
        '勝ち平均時間': g[g['実現損益（円貨）'] > 0]['保有時間'].mean() if not g[g['実現損益（円貨）'] > 0].empty else pd.NaT,
        '負け平均時間': g[g['実現損益（円貨）'] < 0]['保有時間'].mean() if not g[g['実現損益（円貨）'] < 0].empty else pd.NaT,
        '1ロット利益': g[g['実現損益（円貨）'] > 0]['1ロットあたり損益'].mean(),
        '1ロット損失': g[g['実現損益（円貨）'] < 0]['1ロットあたり損益'].mean(),
    })).reset_index()

    summary['勝率'] = (summary['勝ち数'] / summary['取引回数'] * 100).fillna(0)
    summary['総利益'] = df[df['実現損益（円貨）'] > 0].groupby(group_by_cols)['実現損益（円貨）'].sum().reindex(summary.set_index(group_by_cols).index).fillna(0).values
    summary['総損失'] = df[df['実現損益（円貨）'] < 0].groupby(group_by_cols)['実現損益（円貨）'].sum().reindex(summary.set_index(group_by_cols).index).fillna(0).values
    summary['PF'] = (summary['総利益'] / abs(summary['総損失'])).fillna(float('inf'))
    summary['平均利益'] = (summary['総利益'] / summary['勝ち数']).fillna(0)
    summary['平均損失'] = (summary['総損失'] / summary['負け数']).fillna(0)
    summary['RR'] = (summary['平均利益'] / abs(summary['平均損失'])).fillna(float('inf'))

    return summary

def style_and_format_summary(df: pd.DataFrame):
    def format_timedelta(td):
        if pd.isna(td):
            return 'N/A'
        days = td.days
        hours, remainder = divmod(td.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f'{days}日{hours}時間{minutes}分'

    format_dict = {
        '総損益': '{:,.0f}',
        '売買損益': '{:,.0f}',
        'スワップ': '{:,.0f}',
        '取引回数': '{:,.0f}',
        '勝ち数': '{:,.0f}',
        '負け数': '{:,.0f}',
        '総利益': '{:,.0f}',
        '総損失': '{:,.0f}',
        '勝率': '{:.1f}%',
        'PF': '{:.2f}',
        '平均利益': '{:,.1f}',
        '平均損失': '{:,.1f}',
        'RR': '{:.2f}',
        '1ロット利益': '{:,.1f}',
        '1ロット損失': '{:,.1f}',
        '勝ち平均時間': format_timedelta,
        '負け平均時間': format_timedelta,
    }

    def color_positive_negative(val):
        if not pd.api.types.is_number(val) or pd.isna(val): return ''
        color = 'blue' if val > 0 else 'red' if val < 0 else 'black'
        return f'color: {color}'

    def color_win_rate(val):
        if not pd.api.types.is_number(val) or pd.isna(val): return ''
        color = 'blue' if val >= 50 else 'red'
        return f'color: {color}'

    def color_ratio(val):
        if val == float('inf'): return 'color: blue'
        if not pd.api.types.is_number(val) or pd.isna(val): return ''
        color = 'blue' if val >= 1 else 'red'
        return f'color: {color}'

    styler = (df.style
        .applymap(color_positive_negative, subset=[col for col in ['総損益', '売買損益', 'スワップ'] if col in df.columns])
        .applymap(color_win_rate, subset=['勝率'])
        .applymap(color_ratio, subset=['PF', 'RR'])
        .format(format_dict, na_rep='N/A')
    )
    return styler

# --- Streamlit App ---
st.set_page_config(layout="wide")
st.title('📈 GMO取引履歴分析アプリ')


uploaded_file = st.file_uploader("CSVファイルをアップロード", type=['csv'])

if uploaded_file is not None:
    try:
        with st.spinner('ファイルを読み込み、分析中です...'):
            string_io = io.StringIO(uploaded_file.getvalue().decode('shift_jis'))
            
            columns = [
                "約定日時","取引区分","受渡日","約定番号","銘柄名","銘柄コード","限月","コールプット区分","権利行使価格","権利行使価格通貨","カバードワラント商品種別","売買区分","通貨","受渡通貨","市場","口座","信用区分","約定数量","約定単価","コンバージョンレート","手数料","手数料消費税","建単価","新規手数料","新規手数料消費税","管理費","名義書換料","金利","貸株料","品貸料","前日分値洗","経過利子（円貨）","経過利子（外貨）","経過日数（外債）","所得税（外債）","地方税（外債）","金利・価格調整額（CFD）","配当金調整額（CFD）","金利・価格調整額（くりっく株365）","配当金調整額（くりっく株365）","売建単価（くりっく365/くりっく株365）","買建単価（くりっく365/くりっく株365）","円貨スワップ損益","外貨スワップ損益","約定金額（円貨）","約定金額（外貨）","決済金額（円貨）","決済金額（外貨）","実現損益（円貨）","実現損益（外貨）","実現損益（円換算額）","受渡金額（円貨）","受渡金額（外貨）","備考"
            ]
            df = pd.read_csv(string_io, header=0, names=columns)

            df['約定日時'] = pd.to_datetime(df['約定日時'])
            df['adjusted_date'] = df['約定日時'].apply(get_adjusted_date)
            df['決済年月'] = df['adjusted_date'].dt.strftime('%Y-%m')
            df['決済日'] = df['adjusted_date'].dt.strftime('%Y-%m-%d')

            swap_df = df[df['取引区分'].str.contains('スワップ', na=False)].copy()
            total_swap_profit = swap_df['実現損益（円貨）'].sum()
            monthly_swap_summary = swap_df.groupby('決済年月')['実現損益（円貨）'].sum().reset_index()
            daily_swap_summary = swap_df.groupby('決済日')['実現損益（円貨）'].sum().reset_index()

            analyzed_df = process_trades(df.copy())

            if not analyzed_df.empty:
                fx_df = analyzed_df[analyzed_df['取引種別'] == 'FX']
                cfd_df = analyzed_df[analyzed_df['取引種別'] == 'CFD']

                # --- FXセクション ---
                st.subheader('📊 FXサマリー')
                if not fx_df.empty:
                    fx_total_summary = analyze_summary(fx_df.copy(), ['取引種別'])
                    fx_total_summary.rename(columns={'総損益': '売買損益'}, inplace=True)
                    fx_total_summary['スワップ'] = total_swap_profit
                    fx_total_summary['総損益'] = fx_total_summary['売買損益'] + fx_total_summary['スワップ']
                    fx_total_summary = fx_total_summary.rename(columns={'取引種別': '種類'})
                    
                    fx_total_display_order = ['総損益', '売買損益', 'スワップ', '取引回数', '勝ち数', '負け数', '勝率', '総利益', '総損失','PF','平均利益', '平均損失', 'RR', '1ロット利益', '1ロット損失', '勝ち平均時間', '負け平均時間']
                    st.dataframe(style_and_format_summary(fx_total_summary.set_index('種類')[fx_total_display_order]), use_container_width=True)

                    st.markdown("**FX 日次グラフ**")
                    fx_daily_summary = analyze_summary(fx_df, ['決済日'])
                    fx_daily_for_chart = fx_daily_summary[['決済日', '総損益']]
                    daily_swap_for_chart = daily_swap_summary.rename(columns={'実現損益（円貨）': 'スワップ損益'})
                    chart_df_daily = pd.merge(fx_daily_for_chart, daily_swap_for_chart, on='決済日', how='outer').fillna(0)
                    chart_df_daily['日次合計損益'] = chart_df_daily['総損益'] + chart_df_daily['スワップ損益']
                    chart_df_daily['累積損益'] = chart_df_daily['日次合計損益'].cumsum()
                    st.line_chart(chart_df_daily.set_index('決済日')['累積損益'])

                    st.markdown("**FX 月別サマリー**")
                    fx_monthly_summary = analyze_summary(fx_df, ['決済年月'])
                    fx_monthly_display = fx_monthly_summary.rename(columns={'総損益': '売買損益'})
                    monthly_swap_df = monthly_swap_summary.rename(columns={'実現損益（円貨）': 'スワップ'})
                    combined_monthly = pd.merge(fx_monthly_display, monthly_swap_df, on='決済年月', how='outer')
                    numeric_cols = ['売買損益', 'スワップ', '取引回数', '勝ち数', '負け数', '総利益', '総損失', '勝率', 'PF', '平均利益', '平均損失', 'RR', '1ロット利益', '1ロット損失']
                    for col in numeric_cols:
                        if col in combined_monthly.columns:
                            combined_monthly[col] = combined_monthly[col].fillna(0)
                    combined_monthly['総損益'] = combined_monthly['売買損益'] + combined_monthly['スワップ']
                    fx_monthly_display_order = ['決済年月', '総損益', '売買損益', 'スワップ', '取引回数', '勝ち数', '負け数', '勝率', '総利益', '総損失','PF','平均利益', '平均損失', 'RR', '1ロット利益', '1ロット損失', '勝ち平均時間', '負け平均時間']
                    st.dataframe(style_and_format_summary(combined_monthly.sort_values(by='決済年月')[fx_monthly_display_order]), use_container_width=True)

                    st.markdown("**FX 銘柄別サマリー**")
                    fx_symbol_summary = analyze_summary(fx_df, ['銘柄名', 'ポジション'])
                    symbol_swap_df = swap_df.groupby('銘柄名')['実現損益（円貨）'].sum().reset_index().rename(columns={'実現損益（円貨）': 'スワップ'})
                    combined_symbol = pd.merge(fx_symbol_summary, symbol_swap_df, on='銘柄名', how='left')
                    numeric_cols_symbol = ['総損益', 'スワップ', '取引回数', '勝ち数', '負け数', '総利益', '総損失', '勝率', 'PF', '平均利益', '平均損失', 'RR', '1ロット利益', '1ロット損失']
                    for col in numeric_cols_symbol:
                        if col in combined_symbol.columns:
                            combined_symbol[col] = combined_symbol[col].fillna(0)
                    combined_symbol.rename(columns={'総損益': '売買損益'}, inplace=True)
                    combined_symbol['総損益'] = combined_symbol['売買損益'] + combined_symbol['スワップ']
                    fx_symbol_display_order = ['銘柄名', 'ポジション', '総損益', '売買損益', 'スワップ', '取引回数', '勝ち数', '負け数', '勝率', '総利益', '総損失','PF','平均利益', '平均損失', 'RR', '1ロット利益', '1ロット損失', '勝ち平均時間', '負け平均時間']
                    st.dataframe(style_and_format_summary(combined_symbol[fx_symbol_display_order]), use_container_width=True)
                else:
                    st.info('FXの取引データはありませんでした。')

                # --- CFDセクション ---
                st.subheader('📈 CFDサマリー')
                if not cfd_df.empty:
                    cfd_total_summary = analyze_summary(cfd_df.copy(), ['取引種別']).rename(columns={'取引種別': '種類'})
                    st.dataframe(style_and_format_summary(cfd_total_summary.set_index('種類')), use_container_width=True)

                    st.markdown("**CFD 日次グラフ**")
                    cfd_daily_summary = analyze_summary(cfd_df, ['決済日'])
                    cfd_daily_summary['累積損益'] = cfd_daily_summary['総損益'].cumsum()
                    st.line_chart(cfd_daily_summary.set_index('決済日')['累積損益'])

                    st.markdown("**CFD 月別サマリー**")
                    cfd_monthly_summary = analyze_summary(cfd_df, ['決済年月'])
                    st.dataframe(style_and_format_summary(cfd_monthly_summary), use_container_width=True)

                    st.markdown("**CFD 銘柄別サマリー**")
                    cfd_symbol_summary = analyze_summary(cfd_df, ['銘柄名', 'ポジション'])
                    st.dataframe(style_and_format_summary(cfd_symbol_summary), use_container_width=True)
                else:
                    st.info('CFDの取引データはありませんでした。')

            else:
                st.warning('分析可能な決済取引データが見つかりませんでした。')

    except Exception as e:
        st.error(f"エラーが発生しました: {e}")
        st.error("ファイルの形式が正しくないか、予期せぬデータが含まれている可能性があります。")

else:
    st.info('CSVファイルをアップロードして分析を開始してください。')

st.info(
    """
    使い方\r\n
    1. GMOクリック証券の取引履歴CSVファイルをダウンロードしてください。（GMOクリック証券のマイページにログイン後、CFDまたはFXネオ→積算表\→FXネオ取引口座の取引とスワップを☑、CFDの取引を☑→分析したい期間を選択→検索のボタンをクリック→下段のダウンロードを選択）
    2. ダウンロードしたCSVファイルをこのwebアプリにアップロードしてください。\r\n
    ---harunami---
    """
)
