"""Read-only request review; approval and rejection remain in Telegram."""

from gui.tabs.records_tab import RecordsTab


class RequestsTab(RecordsTab):
    def __init__(self, repository, runner, theme, parent=None):
        super().__init__(repository, runner, theme, [
            ("request_id", "Request ID"), ("customer", "Customer"), ("channel", "Channel"),
            ("device_id", "Device ID"), ("request_status", "Request status"),
            ("delivery_status", "Customer delivery"), ("created_at", "Created · local"),
        ], "request_id", title="Requests", status_filter=True,
            note="Read-only. Approve or reject requests in Telegram with /requests.", parent=parent)
        for column, width in enumerate((210, 155, 95, 240, 130, 200, 185)):
            self.table.setColumnWidth(column, width)
