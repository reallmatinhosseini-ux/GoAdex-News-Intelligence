"""Single place that instantiates providers (swap implementations here)."""
from .keyed import FRED, TradingEconomics, TwelveData
from .calendar_ff import ForexFactory
from .gold import Dukascopy, GoldSpot, LiveQuotes
from .llm import Claude
from .news import NewsFeeds
from .official import BEASchedule, BLS, CFTC, ECB, FedCalendar, FedPress, Treasury

fed_calendar = FedCalendar()
fed_press = FedPress()
bea = BEASchedule()
bls = BLS()
treasury = Treasury()
cftc = CFTC()
ecb = ECB()
live = LiveQuotes()
gold_spot = GoldSpot(live)
duka = Dukascopy()
ff = ForexFactory()
fred = FRED()
prices = TwelveData()
te = TradingEconomics()
claude = Claude()
news = NewsFeeds()

ALL = [live, gold_spot, duka, ff, news, fed_calendar, fed_press, bea, bls, treasury, cftc, ecb, fred, prices, te, claude]


def register_all():
    for p in ALL:
        p.register()
