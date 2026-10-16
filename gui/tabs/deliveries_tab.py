"""Read-only delivery history, distinct from offline activation."""

from gui.tabs.records_tab import RecordsTab


class DeliveriesTab(RecordsTab):
    def __init__(self, repository, runner, theme, parent=None):
        super().__init__(repository, runner, theme, [
            ("delivery_id", "ID"), ("purpose", "Purpose"), ("request_id", "Request ID"),
            ("license_id", "License ID"), ("attempt_number", "Attempt"), ("destination", "Destination"),
            ("status", "Delivery status"), ("started_at", "Started · local"),
            ("finished_at", "Finished · local"), ("error_category", "Safe error"),
        ], "delivery_id", title="Delivery History",
            note="Sent means Telegram acknowledgment, not V-CODE activation. UNKNOWN needs admin review; no resend here. Stored lease states are refreshed by the bot.", parent=parent)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(4, 75)
        self.table.setColumnWidth(6, 220)
