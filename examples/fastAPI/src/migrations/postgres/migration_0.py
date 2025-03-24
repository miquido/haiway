from integrations.postgres import PostgresConnection


async def execute_migration() -> None:
    await PostgresConnection.execute(
        """\
        CREATE TABLE IF NOT EXISTS
            todos (
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                modified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                description TEXT NOT NULL,
                completed BOOL NOT NULL DEFAULT false,
                PRIMARY KEY(id)
            )
        ;\
        """
    )
