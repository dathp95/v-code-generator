"""Profile validation and authorization. Does not mutate business snapshots or tokens."""
import unicodedata
from storage.repositories.customer_repository import CustomerRepository


class CustomerValidationError(ValueError):
    """Safe field/authorization error with no raw input."""


def validate_metadata(value, *, name='Company', required=False):
    # Reject controls before trimming so newline/tab input is not silently accepted.
    if not isinstance(value, str) or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value):
        raise CustomerValidationError(f'{name}: control characters are not allowed / không dùng ký tự điều khiển.')
    value = value.strip()
    if len(value) > 150 or (required and not value):
        raise CustomerValidationError(f'{name}: enter {"1–150" if required else "0–150"} characters / ký tự.')
    return value


class CustomerService:
    def __init__(self, database):
        self.database = database
        self.customers = CustomerRepository(database)

    def ensure(self, channel, user_id, display_name, *, connection=None):
        if connection is None:
            with self.database.transaction() as connection:
                return self.ensure(channel, user_id, display_name, connection=connection)
        # Telegram names are an initial label only. Never overwrite a saved/admin-edited name.
        label = ''.join(c for c in str(display_name) if unicodedata.category(c) not in ('Cc', 'Cf', 'Cs')).strip()[:150] or str(user_id)
        return self.customers.add_if_missing(channel, user_id, label, connection=connection)

    def update_owned_company(self, *, channel, user_id, actor_user_id, chat_type, company):
        if channel != 'telegram' or str(user_id) != str(actor_user_id) or chat_type != 'private':
            raise CustomerValidationError('Only the owning customer in private chat may change Company.')
        company = validate_metadata(company)
        with self.database.transaction() as connection:
            profile = self.customers.get(channel, user_id, connection=connection)
            if profile is None:
                raise CustomerValidationError('Customer profile unavailable. Start registration again.')
            return self.customers.update(channel, user_id, profile['display_name'], company, connection=connection)

    def edit_admin(self, *, channel, user_id, display_name, company):
        """Local admin desktop action; identity fields are immutable in the editor."""
        display_name = validate_metadata(display_name, name='Name', required=True)
        company = validate_metadata(company)
        with self.database.transaction() as connection:
            if not self.customers.get(channel, user_id, connection=connection):
                raise CustomerValidationError('Customer profile no longer exists. Refresh and select again.')
            return self.customers.update(channel, user_id, display_name, company, connection=connection)
