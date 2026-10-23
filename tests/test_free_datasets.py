"""Parsers for the free datasets collected from 2026-10-18, on small inline samples (no network)."""
import io
import zipfile
from datetime import date, datetime

from financial_brain.ingest import bulk_deals as BD
from financial_brain.ingest import flows as FL
from financial_brain.ingest import index_tri as IT
from financial_brain.ingest import macro as MC
from financial_brain.ingest import nse_archives as NA
from financial_brain.ingest import shareholding as SH


def _zip(name, text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(name, text)
    return b.getvalue()


def test_fo_bhav_both_formats_share_one_shape():
    old = ("INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
           "FUTSTK,INFY,28-Jan-2016,0,XX,1100,1110,1090,1105,1105,100,55.2,5000,200,04-JAN-2016,\n"
           "OPTSTK,INFY,28-Jan-2016,1100,CE,20,22,18,21,21,10,1.1,300,-5,04-JAN-2016,\n")
    new = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,"
           "OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal\n"
           "2026-09-16,2026-09-16,FO,NSE,STF,1,,INFY,,2026-09-29,2026-09-29,,,INFY26SEPFUT,1500,1510,1490,1505,1505,1500,1504,1505,9000,100,50,7.5e7\n"
           "2026-09-16,2026-09-16,FO,NSE,STO,2,,INFY,,2026-09-29,2026-09-29,1500,PE,INFY26SEP1500PE,10,12,9,11,11,10,1504,11,800,-20,30,3.3e5\n")
    a = NA.parse_fo_bhav(_zip("fo04JAN2016bhav.csv", old), date(2016, 1, 4))
    b = NA.parse_fo_bhav(_zip("BhavCopy.csv", new), date(2026, 9, 16))
    assert list(a.columns) == list(b.columns)
    assert list(a["instrument"]) == ["FUTSTK", "OPTSTK"] and list(b["instrument"]) == ["FUTSTK", "OPTSTK"]
    assert a["value_inr"].iloc[0] == 55.2e5 and a["option_type"].iloc[0] == "XX" and b["option_type"].iloc[0] == "XX"
    assert b["strike"].iloc[0] == 0.0 and b["option_type"].iloc[1] == "PE" and b["oi"].iloc[1] == 800


def test_ban_list_and_participant_positions():
    assert NA.parse_ban(b"Securities in Ban For Trade Date 16-SEP-2026:\n1,SAIL\n2,RBLBANK\n") == ["SAIL", "RBLBANK"]
    assert NA.parse_ban(b"Securities in Ban For Trade Date 17-SEP-2026: NIL\n") == []
    csv = ('"Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Sep 16, 2026",,,\r\n'
           "Client Type,Future Index Long,Future Index Short\r\nClient,100,200\r\nFII,300,50\r\nTOTAL,400,250\r\n")
    p = NA.parse_participant(csv.encode(), date(2026, 9, 16))
    fii = p[(p.client_type == "FII") & (p.measure == "Future Index Long")]["contracts"].iloc[0]
    assert len(p) == 6 and fii == 300


def test_bulk_deals_csv():
    text = ('﻿"Date ","Symbol ","Security Name ","Client Name ","Buy / Sell ","Quantity Traded ","Trade Price / Wght. Avg. Price ","Remarks "\n'
            '"01-OCT-2026","ADVANCE","Advance Agrolife Limited","GROWVIA","BUY","3,50,000","116.75","-"\n')
    r = BD.parse(text)
    assert r[0]["symbol"] == "ADVANCE" and BD._num(r[0]["quantity traded"]) == 350000.0
    assert BD._date(r[0]["date"]) == date(2026, 10, 1)


def test_shareholding_known_at_is_the_later_time_and_facts_are_category_totals():
    r = {"broadcastDate": "07-JAN-2022 00:10:01", "submissionDate": "19-OCT-2021"}
    assert SH.known_at(r) == datetime(2022, 1, 7, 0, 10, 1)
    x = (b'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:s="http://www.bseindia.com/xbrl/shp/2024">'
         b'<s:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MutualFundsOrUTI_ContextI">0.23</s:ShareholdingAsAPercentageOfTotalNumberOfShares>'
         b'<s:NumberOfShares contextRef="IndividualsOrHUF_Context15">100</s:NumberOfShares>'
         b'<s:NameOfTheShareholder contextRef="MutualFundsOrUTI_ContextI">X</s:NameOfTheShareholder></xbrli:xbrl>')
    assert SH.parse_xbrl(x) == [("mutualfundsoruti", "ShareholdingAsAPercentageOfTotalNumberOfShares", 0.23)]
    assert SH.category("MutualFundsOrUtiI") == SH.category("MutualFundsOrUTI_ContextI") == "mutualfundsoruti"
    assert SH.category("InstitutionsForeignPortfolioInvestorCatergoryOneI") == "institutionsforeignportfolioinvestorcategoryone"
    assert SH.category("DetailsSharesHeldByIndividualsOrHUF001I") is None and SH.category("OneI") is None
    assert SH.category("IndividualsOrHUF_Context15") is None


def test_macro_tri_and_flows():
    assert MC.parse(b"observation_date,DEXINUS\n2026-10-08,.\n2026-10-09,96.73\n") == [(date(2026, 10, 9), 96.73)]
    t = IT.parse_tri(b'[{"Date":"16 Oct 2026","TotalReturnsIndex":"35831.71","NTR_Value":"32939.02"}]')
    assert t == [(date(2026, 10, 16), 35831.71, 32939.02)]
    f = FL.parse(b'[{"buyValue":"17201.8","category":"DII","date":"16-Oct-2026","netValue":"-3661.28","sellValue":"20863.08"}]')
    assert f == [(date(2026, 10, 16), "DII", 17201.8, 20863.08, -3661.28)]


def test_archive_urls_switch_format_on_the_udiff_date():
    assert NA.url("fo_bhav", date(2024, 7, 5)).endswith("/DERIVATIVES/2024/JUL/fo05JUL2024bhav.csv.zip")
    assert NA.url("fo_bhav", date(2024, 7, 8)).endswith("BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip")


def test_depth_archive_picks_near_futures_stocks_and_options_around_the_money():
    from financial_brain.ingest import depth_archive as DA

    class KD:
        @staticmethod
        def instruments(ex):
            rows = [{"instrument_type": "FUT", "name": "INFY", "tradingsymbol": "INFY26OCTFUT", "expiry": "2026-10-27"},
                    {"instrument_type": "FUT", "name": "INFY", "tradingsymbol": "INFY26NOVFUT", "expiry": "2026-11-24"},
                    {"instrument_type": "FUT", "name": "NIFTY", "tradingsymbol": "NIFTY26OCTFUT", "expiry": "2026-10-27"}]
            rows += [{"instrument_type": t, "name": "NIFTY", "tradingsymbol": f"NIFTY{int(k)}{t}", "expiry": "2026-10-20",
                      "strike": k} for k in range(24000, 26050, 50) for t in ("CE", "PE")]
            return rows
    s = DA.instruments_for_today(KD, {"NIFTY": 25000.0})
    assert "NFO:INFY26OCTFUT" in s and "NFO:INFY26NOVFUT" not in s and "NSE:INFY" in s and "NSE:NIFTY" not in s
    opts = [x for x in s if x.endswith(("CE", "PE"))]
    assert len(opts) == 42 and "NFO:NIFTY25000CE" in opts and "NFO:NIFTY24500PE" in opts and "NFO:NIFTY24450PE" not in opts


def test_rating_direction():
    from financial_brain.events import rating_direction as RD
    assert RD.direction("CRISIL has downgraded the long term rating to AA-; outlook stable") == "downgrade"
    assert RD.direction("ICRA upgraded the rating of the company's NCDs") == "upgrade"
    assert RD.direction("CARE reaffirmed the ratings of bank facilities") == "reaffirmed"
    assert RD.direction("India Ratings: outlook revised to Negative from Stable") == "outlook_negative"
    assert RD.direction("Rating placed on watch with negative implications") == "watch_negative"
    assert RD.direction("Credit rating withdrawn at the request of the company") == "withdrawn"
    assert RD.direction("Intimation of credit rating") == "unclear"
    assert RD.SIGN[RD.direction("downgraded")] == -1


def test_ipo_lockins_and_mospi_availability():
    from financial_brain.ingest import mospi as MO
    from financial_brain.ingest import nse_disclosures as ND
    d = dict(ND.lockin_dates(date(2026, 1, 16)))
    assert d["anchor_half"] == date(2026, 2, 14) and d["anchor_rest"] == date(2026, 4, 15)
    assert MO.available_on("CPI", date(2025, 12, 1)) == date(2026, 1, 13)
    assert MO.available_on("IIP", date(2026, 8, 1)) == date(2026, 10, 13)
    row = MO.parse("CPI", b'{"data":[{"baseyear":"2012","year":2025,"month":"August","sector":"Combined",'
                          b'"subgroup":"General-Overall","index":"197.0","inflation":"2.07","status":"F"}]}')
    assert row[0][1] == date(2025, 8, 1) and row[0][5] == 197.0 and row[0][8] == date(2025, 9, 13)


def test_monthly_update_kinds():
    from financial_brain.ingest import monthly_updates as MU
    assert MU.kind_of("Maruti Suzuki - Sales for the month of September 2026 (units)") == "auto_sales"
    assert MU.kind_of("UltraTech: cement despatch volumes for October") == "cement_steel_volumes"
    assert MU.kind_of("NTPC power generation for the month") == "power_generation"
    assert MU.kind_of("Business update for the quarter - AUM as on 30 September") == "business_update"
    assert MU.kind_of("Outcome of board meeting") is None
