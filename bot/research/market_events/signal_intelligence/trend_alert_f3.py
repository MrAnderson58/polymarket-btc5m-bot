"""Phase F.3 — deferred ranked Telegram alert after full intel pipeline."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def send_deferred_trend_alert(conn: Any, event_id: int) -> bool:
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert
    from bot.research.market_events.signal_intelligence.config import (
        F41_TELEGRAM_DEDUPE,
        TREND_PREMIUM_TELEGRAM,
        TREND_SIGNAL_RANKING,
        TREND_SHOCK_ENABLED,
    )
    from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import MSG_SHOCK

    if not TREND_SHOCK_ENABLED or not alert_shock_enabled():
        return False

    if TREND_SIGNAL_RANKING:
        from bot.research.market_events.signal_intelligence.signal_ranking_f3 import (
            mark_alert_sent,
            should_send_ranked_alert,
        )
        if not should_send_ranked_alert(conn, event_id):
            logger.debug("trend alert skipped by ranking event=%s", event_id)
            return False

    if TREND_PREMIUM_TELEGRAM:
        from bot.research.market_events.signal_intelligence.config import F4_TELEGRAM_FORMAT
        if F4_TELEGRAM_FORMAT:
            from bot.research.market_events.signal_intelligence.telegram_f4 import render_final_telegram_f4
            msg = render_final_telegram_f4(conn, event_id)
        else:
            from bot.research.market_events.signal_intelligence.telegram_trend_premium import (
                render_trend_premium_v2,
            )
            msg = render_trend_premium_v2(conn, event_id)
    else:
        from bot.research.market_events.signal_intelligence.telegram_f3 import format_shock_f3
        msg = format_shock_f3(conn, event_id)

    sent = _safe_alert(
        conn,
        event_id=event_id,
        alert_type=ALERT_SHOCK,
        detail="trend_premium_v2",
        message=msg,
        enabled=True,
        message_type=MSG_SHOCK if F41_TELEGRAM_DEDUPE else None,
    )
    if sent and TREND_SIGNAL_RANKING:
        from bot.research.market_events.signal_intelligence.signal_ranking_f3 import mark_alert_sent
        mark_alert_sent(conn, event_id)
    return sent
