"""Customer metadata keyed only by persisted channel and user ID."""
from storage.repositories.pagination import query_page, search_pattern

SELECT = "SELECT channel || ':' || user_id AS profile_id, * FROM customer_profiles"


class CustomerRepository:
    def __init__(self, database):
        self.database = database

    def get(self, channel, user_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.get(channel, user_id, connection=connection)
        row = connection.execute(SELECT + ' WHERE channel = ? AND user_id = ?', (channel, str(user_id))).fetchone()
        return dict(row) if row else None

    def add_if_missing(self, channel, user_id, display_name, *, connection):
        now = self.database.timestamp()
        connection.execute('''INSERT INTO customer_profiles (channel, user_id, display_name, company, created_at, updated_at)
            VALUES (?, ?, ?, '', ?, ?) ON CONFLICT(channel, user_id) DO NOTHING''',
            (channel, str(user_id), display_name, now, now))
        return self.get(channel, user_id, connection=connection)

    def update(self, channel, user_id, display_name, company, *, connection):
        connection.execute('UPDATE customer_profiles SET display_name = ?, company = ?, updated_at = ? WHERE channel = ? AND user_id = ?',
                           (display_name, company, self.database.timestamp(), channel, str(user_id)))
        return self.get(channel, user_id, connection=connection)

    def list_page(self, *, search='', page=0, size=25, sort='updated_at', descending=True):
        pattern = search_pattern(search)
        where = "(display_name LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\' OR user_id LIKE ? ESCAPE '\\')"
        sorts = {key: key for key in ('display_name', 'company', 'user_id', 'channel', 'created_at', 'updated_at', 'profile_id')}
        with self.database.connection() as connection:
            return query_page(connection, SELECT, where, [pattern] * 3, sorts=sorts, sort=sort,
                              descending=descending, page=page, size=size)
