"""Read-only order metadata. Payment confirmation remains in Telegram."""
from gui.tabs.records_tab import RecordsTab
from storage.repositories.subscription_repository import STATUSES


class OrdersTab(RecordsTab):
    def __init__(self, repository, runner, theme, parent=None):
        super().__init__(repository, runner, theme, [
            ('reference', 'Order reference'), ('customer', 'Customer'), ('device_id', 'Machine ID'),
            ('months', 'Months'), ('total_vnd', 'Amount · VND'), ('payment_status', 'Payment'),
            ('request_id', 'Request ID'), ('license_id', 'License ID'), ('delivery_status', 'Delivery'),
            ('created_at', 'Created · local'),
        ], 'order_id', title='Orders', status_filter=True,
            note='Read-only. Payment, issuance and Telegram acknowledgement are separate. Use /orders in Telegram to review payment.', parent=parent)
        self.sort = 'created_at'
        self.status.blockSignals(True)
        self.status.clear()
        self.status.addItems(['all', *STATUSES])
        self.status.blockSignals(False)
        for column, width in enumerate((260, 160, 240, 75, 135, 160, 220, 220, 170, 185)):
            self.table.setColumnWidth(column, width)
