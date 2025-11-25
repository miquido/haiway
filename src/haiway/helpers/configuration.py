from collections.abc import MutableMapping, Sequence
from typing import Any, Literal, Protocol, Self, overload, runtime_checkable

from haiway.attributes import State
from haiway.context import ctx
from haiway.helpers.statemethods import statemethod
from haiway.types import META_EMPTY, Meta

__all__ = (
    "Configuration",
    "ConfigurationInvalid",
    "ConfigurationMissing",
    "ConfigurationRepository",
)


class ConfigurationMissing(Exception):
    """Raised when a required configuration is not found.

    This exception is thrown when attempting to load a configuration with
    `required=True` but no configuration data is available for the given
    identifier.

    Attributes:
        identifier: The configuration identifier that was not found.

    Example:
        ```python
        try:
            config = await MyConfig.load(required=True)
        except ConfigurationMissing as exc:
            print(f"Missing config: {exc.identifier}")
            # Handle missing configuration...
        ```
    """

    __slots__ = ("identifier",)

    def __init__(
        self,
        *,
        identifier: str,
    ) -> None:
        super().__init__(f"Missing configuration for '{identifier}'")
        self.identifier: str = identifier


class ConfigurationInvalid(Exception):
    """Raised when configuration data fails validation during loading.

    This exception is thrown when configuration data is found but cannot
    be converted to the target Configuration class due to type validation
    errors or missing required fields.

    Attributes:
        identifier: The configuration identifier that failed validation.
        reason: Detailed description of the validation failure.

    Example:
        ```python
        try:
            config = await DatabaseConfig.load()
        except ConfigurationInvalid as exc:
            print(f"Invalid config {exc.identifier}: {exc.reason}")
            # Handle invalid configuration data...
        ```
    """

    __slots__ = (
        "identifier",
        "reason",
    )

    def __init__(
        self,
        *,
        identifier: str,
        reason: str,
    ) -> None:
        super().__init__(f"Invalid configuration for '{identifier}': {reason}")
        self.identifier: str = identifier
        self.reason: str = reason


class Configuration(State):
    """Base class for typed configuration objects.

    Configuration classes inherit from State and provide type-safe loading
    from configuration repositories. They support various loading patterns
    including optional loading, required loading, and loading with defaults.

    The class uses the current ConfigurationRepository from the context to
    load configuration data, converting it to the appropriate typed instance
    using Haiway's State validation system.

    Example:
        ```python
        class DatabaseConfig(Configuration):
            host: str = "localhost"
            port: int = 5432
            username: str
            password: str
            timeout: float = 30.0

        # Optional loading - returns None if not found
        config = await DatabaseConfig.load()
        if config is not None:
            connect_to_db(config.host, config.port)

        # Required loading - raises ConfigurationMissing if not found
        config = await DatabaseConfig.load(required=True)

        # Loading with default fallback
        config = await DatabaseConfig.load(
            default=DatabaseConfig(username="admin", password="secret")
        )

        # Custom identifier (defaults to class __qualname__)
        config = await DatabaseConfig.load(identifier="prod_db_config")
        ```
    """

    @overload
    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        **extra: Any,
    ) -> Self | None:
        """Load configuration optionally.

        Args:
            identifier: Configuration identifier. Defaults to class __qualname__.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Configuration instance if found, None otherwise.
        """
        ...

    @overload
    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        *,
        default: Self,
        **extra: Any,
    ) -> Self:
        """Load configuration with a default fallback.

        Args:
            identifier: Configuration identifier. Defaults to class __qualname__.
            default: Default configuration instance to return if not found.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Configuration instance from repository or the provided default.
        """
        ...

    @overload
    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        *,
        required: Literal[True],
        **extra: Any,
    ) -> Self:
        """Load configuration as required.

        Args:
            identifier: Configuration identifier. Defaults to class __qualname__.
            required: Must be True to use this overload.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Configuration instance from repository or default instance from class.

        Raises:
            ConfigurationMissing: If configuration is not found and class cannot be
                instantiated with defaults.
            ConfigurationInvalid: If configuration data fails validation.

        Note:
            If the configuration is not found in the repository, this method first attempts
            to resolve a contextual instance bound via ctx.state(config). If no contextual
            state is available, it will try to create a default instance by calling the
            configuration class constructor with no arguments. Only if both strategies
            fail will ConfigurationMissing be raised.
        """
        ...

    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        *,
        default: Self | None = None,
        required: bool = False,
        **extra: Any,
    ) -> Self | None:
        """Load configuration from the current repository.

        This method delegates to ConfigurationRepository.load() using the
        current repository instance from the context.

        Args:
            identifier: Configuration identifier. Defaults to class __qualname__.
            default: Default instance to return if not found.
            required: Whether to raise an exception if not found.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Configuration instance, default, or None based on parameters.

        Raises:
            ConfigurationMissing: If required=True and configuration not found and class
                cannot be instantiated with defaults.
            ConfigurationInvalid: If configuration data fails validation.

        Note:
            When required=True and no configuration is found, this method first tries to
            resolve a contextual instance via ctx.state(cls). If no contextual override
            exists, it attempts to create a default instance by calling the configuration
            class constructor with no arguments. This allows contextual overrides to win
            while still honoring configuration classes that provide defaults.
        """
        return await ConfigurationRepository.load(
            cls,
            identifier=identifier,
            default=default,
            required=required,
            **extra,
        )


@runtime_checkable
class ConfigurationListing(Protocol):
    async def __call__(
        self,
        config: type[Configuration] | None,
        **extra: Any,
    ) -> Sequence[str]: ...


@runtime_checkable
class ConfigurationLoading(Protocol):
    async def __call__[Config: Configuration](
        self,
        config: type[Config],
        identifier: str,
        **extra: Any,
    ) -> Config | None: ...


@runtime_checkable
class ConfigurationDefining(Protocol):
    async def __call__(
        self,
        identifier: str,
        value: Configuration,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class ConfigurationRemoving(Protocol):
    async def __call__(
        self,
        identifier: str,
        **extra: Any,
    ) -> None: ...


async def _empty_listing(
    config: type[Configuration] | None,
    **extra: Any,
) -> Sequence[str]:
    return ()


async def _none_loading[Config: Configuration](
    config: type[Config],
    identifier: str,
    **extra: Any,
) -> Config | None:
    return None


async def _noop_defining(
    identifier: str,
    value: Configuration,
    **extra: Any,
) -> None:
    pass


async def _noop_removing(
    identifier: str,
    **extra: Any,
) -> None:
    pass


class ConfigurationRepository(State):
    """Central repository for configuration management.

    The ConfigurationRepository manages configuration storage and retrieval through
    pluggable protocol implementations. It provides methods for loading, defining,
    and removing configurations while integrating with Haiway's context system.

    The repository uses protocol-based backends for different operations:
    - ConfigurationListing: List available configuration identifiers
    - ConfigurationLoading: Load configuration data by identifier
    - ConfigurationDefining: Store/update configuration data
    - ConfigurationRemoving: Remove configuration data

    Attributes:
        listing: Protocol implementation for listing configurations (defaults to empty)
        loading: Protocol implementation for loading configurations (defaults to None)
        defining: Protocol implementation for storing configurations (defaults to no-op)
        removing: Protocol implementation for removing configurations (defaults to no-op)

    Example:
        ```python
        # Create repository with custom backends
        class FileStorage(State):
            config_dir: Path

            async def list_configs(self, **extra: Any) -> Sequence[str]:
                return tuple(f.stem for f in self.config_dir.glob("*.json"))

            async def load_config(
                self, identifier: str, **extra: Any
            ) -> Mapping[str, BasicValue] | None:
                config_file = self.config_dir / f"{identifier}.json"
                return json.loads(config_file.read_text()) if config_file.exists() else None

        storage = FileStorage(config_dir=Path("./configs"))
        repo = ConfigurationRepository(
            listing=storage.list_configs,
            loading=storage.load_config
        )

        async with ctx.scope("app", repo):
            # Repository available throughout the scope
            configs = await ConfigurationRepository.configurations()
            my_config = await MyConfig.load("production")
        ```
    """

    @classmethod
    def volatile(  # noqa: C901
        cls,
        *configs: Configuration,
        **named_configs: Configuration,
    ) -> Self:
        """Create a volatile in-memory configuration repository.

        This factory method creates a repository that stores configurations in memory.
        It's useful for testing, development, or applications that don't need persistent
        configuration storage.

        Args:
            *configs: Configuration instances to store using their class __qualname__ as identifier.
            **named_configs: Configuration instances with explicit identifiers.

        Returns:
            ConfigurationRepository instance with in-memory storage.

        Example:
            ```python
            # Store configurations with automatic identifiers
            db_config = DatabaseConfig(host="localhost", database="myapp")
            api_config = APIConfig(base_url="https://api.example.com")

            repo = ConfigurationRepository.volatile(db_config, api_config)

            # Store configurations with custom identifiers
            repo = ConfigurationRepository.volatile(
                production_db=DatabaseConfig(host="prod.db.com", database="prod"),
                staging_db=DatabaseConfig(host="stage.db.com", database="stage")
            )

            async with ctx.scope("app", repo):
                # Load by class name
                db_config = await DatabaseConfig.load()  # Uses DatabaseConfig.__qualname__

                # Load by custom identifier
                prod_db = await DatabaseConfig.load(identifier="production_db")
            ```
        """
        storage: MutableMapping[str, Configuration] = {}
        for element in configs:
            assert isinstance(element, Configuration)  # nosec: B101
            storage[type(element).__qualname__] = element

        for key, element in named_configs.items():
            assert isinstance(element, Configuration)  # nosec: B101
            storage[key] = element

        async def listing(
            config: type[Configuration] | None,
            **extra: Any,
        ) -> Sequence[str]:
            if config is None:
                return tuple(storage.keys())

            configs: list[str] = []
            for key, value in storage.items():
                if type(value).__name__ != config.__name__:
                    continue

                configs.append(key)

            return tuple(configs)

        async def loading[Config: Configuration](
            config: type[Config],
            identifier: str,
            **extra: Any,
        ) -> Config | None:
            stored: Configuration | None = storage.get(identifier, None)
            if stored is None:
                return None

            if isinstance(stored, config):
                return stored

            raise TypeError(
                f"Type of configuration '{identifier}' is {type(stored)}, expected {config}"
            )

        async def defining(
            identifier: str,
            value: Configuration,
            **extra: Any,
        ) -> None:
            storage[identifier] = value

        async def removing(
            identifier: str,
            **extra: Any,
        ) -> None:
            del storage[identifier]

        return cls(
            listing=listing,
            loading=loading,
            defining=defining,
            removing=removing,
            meta=Meta.of({"source": "volatile"}),
        )

    @overload
    @classmethod
    async def configurations(
        cls,
        config: type[Configuration] | None = None,
        **extra: Any,
    ) -> Sequence[str]: ...

    @overload
    async def configurations(
        self,
        config: type[Configuration] | None = None,
        **extra: Any,
    ) -> Sequence[str]: ...

    @statemethod
    async def configurations(
        self,
        config: type[Configuration] | None = None,
        **extra: Any,
    ) -> Sequence[str]:
        """List all available configuration identifiers.

        Uses the current repository's listing protocol to retrieve all available
        configuration identifiers.

        Args:
            config: type[Configuration] | None = None - configuration type to choose
            **extra: Additional parameters passed to the listing protocol.

        Returns:
            Sequence of configuration identifiers available in the repository.

        Example:
            ```python
            async with ctx.scope("app", my_repository):
                identifiers = await ConfigurationRepository.configurations()
                print(f"Available configurations: {', '.join(identifiers)}")
            ```
        """
        return await self.listing(config=config, **extra)

    @overload
    @classmethod
    async def load[Config: Configuration](
        cls,
        config: type[Config],
        /,
        *,
        identifier: str | None = None,
        **extra: Any,
    ) -> Config | None: ...

    @overload
    async def load[Config: Configuration](
        self,
        config: type[Config],
        /,
        *,
        identifier: str | None = None,
        **extra: Any,
    ) -> Config | None:
        """Load configuration optionally by type.

        Args:
            config: Configuration class to load.
            identifier: Configuration identifier. Defaults to config.__qualname__.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Configuration instance if found, None otherwise.
        """
        ...

    @overload
    @classmethod
    async def load[Config: Configuration](
        cls,
        config: type[Config],
        /,
        *,
        identifier: str | None = None,
        default: Config,
        **extra: Any,
    ) -> Config: ...

    @overload
    async def load[Config: Configuration](
        self,
        config: type[Config],
        /,
        *,
        identifier: str | None = None,
        default: Config,
        **extra: Any,
    ) -> Config:
        """Load configuration with default fallback by type.

        Args:
            config: Configuration class to load.
            identifier: Configuration identifier. Defaults to config.__qualname__.
            default: Default configuration instance to return if not found.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Configuration instance from repository or the provided default.
        """
        ...

    @overload
    @classmethod
    async def load[Config: Configuration](
        cls,
        config: type[Config],
        /,
        *,
        identifier: str | None = None,
        required: Literal[True],
        **extra: Any,
    ) -> Config: ...

    @overload
    async def load[Config: Configuration](
        self,
        config: type[Config],
        /,
        *,
        identifier: str | None = None,
        required: Literal[True],
        **extra: Any,
    ) -> Config:
        """Load configuration as required by type.

        Args:
            config: Configuration class to load.
            identifier: Configuration identifier. Defaults to config.__qualname__.
            required: Must be True to use this overload.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Configuration instance from repository or default instance from class.

        Raises:
            ConfigurationMissing: If configuration is not found and class cannot be
                instantiated with defaults.
            ConfigurationInvalid: If configuration data fails validation.

        Note:
            If the configuration is not found in the repository, this method first attempts
            to resolve a contextual instance bound via ctx.state(config). If that also fails,
            it will try to create a default instance by calling the configuration class
            constructor with no arguments. Only if both strategies fail will
            ConfigurationMissing be raised.
        """
        ...

    @overload
    @classmethod
    async def load[Config: Configuration](
        cls,
        config: type[Config],
        /,
        *,
        identifier: str | None,
        default: Config | None,
        required: bool,
        **extra: Any,
    ) -> Config | None: ...

    @statemethod
    async def load[Config: Configuration](
        self,
        config: type[Config],
        /,
        *,
        identifier: str | None = None,
        default: Config | None = None,
        required: bool = False,
        **extra: Any,
    ) -> Config | None:
        """Load typed configuration from the current repository.

        This method provides direct loading of configuration classes with full
        type safety. It handles error logging, validation, and fallback logic.

        Args:
            config: Configuration class to load and validate against.
            identifier: Configuration identifier. Defaults to config.__qualname__.
            default: Default instance to return if not found.
            required: Whether to raise an exception if not found.
            **extra: Additional parameters passed to the loading protocol.

        Returns:
            Typed configuration instance, default, or None based on parameters.

        Raises:
            ConfigurationMissing: If required=True and configuration not found and class
                cannot be instantiated with defaults.
            ConfigurationInvalid: If configuration data fails validation.

        Note:
            When required=True and no configuration is found, this method first attempts
            to resolve a contextual instance via ctx.state(config). If no contextual state
            is available, it tries to create a default instance by calling the configuration
            class constructor with no arguments. This allows contextual overrides to win
            while still honoring configuration classes that provide defaults.

        Example:
            ```python
            # Load with different patterns
            config = await ConfigurationRepository.load(DatabaseConfig)
            config = await ConfigurationRepository.load(
                DatabaseConfig,
                identifier="production_db"
            )
            config = await ConfigurationRepository.load(
                DatabaseConfig,
                default=DatabaseConfig(host="localhost")
            )

            # This will try contextual state, then class defaults if not found in repository
            config = await ConfigurationRepository.load(
                DatabaseConfig,
                required=True
            )
            ```
        """
        config_identifier: str = config.__qualname__ if identifier is None else identifier
        ctx.log_info(f"Loading configuration '{config_identifier}'...")
        loaded: Config | None
        try:
            loaded = await self.loading(
                config,
                identifier=config_identifier,
                **extra,
            )
            if loaded is None:
                ctx.log_info("...configuration missing...")

            else:
                ctx.log_info("...configuration loaded!")
                ctx.record_info(
                    event="configuration.load",
                    attributes={
                        "configuration": config.__qualname__,
                        "identifier": config_identifier,
                        "status": "success",
                    },
                )
                return loaded

        except Exception as exc:
            ctx.log_error(
                f"...failed to load configuration '{config_identifier}'!",
                exception=exc,
            )
            ctx.record_error(
                event="configuration.load",
                attributes={
                    "configuration": config.__qualname__,
                    "identifier": config_identifier,
                    "status": "error",
                },
            )
            raise ConfigurationInvalid(
                identifier=config_identifier,
                reason=str(exc),
            ) from exc

        if default is not None:
            ctx.log_info("...using default!")
            ctx.record_info(
                event="configuration.load",
                attributes={
                    "configuration": config.__qualname__,
                    "identifier": config_identifier,
                    "status": "fallback",
                    "fallback": "default",
                },
            )
            return default

        elif required:
            ctx.log_info("...attempting to use context state...")
            try:
                contextual: Config = ctx.state(config)
                ctx.log_info("...using contextual configuration!")
                ctx.record_info(
                    event="configuration.load",
                    attributes={
                        "configuration": config.__qualname__,
                        "identifier": config_identifier,
                        "status": "fallback",
                        "fallback": "contextual",
                    },
                )
                return contextual

            except Exception:
                ctx.log_error(f"...unavailable configuration '{config_identifier}'!")
                ctx.record_error(
                    event="configuration.load",
                    attributes={
                        "configuration": config.__qualname__,
                        "identifier": config_identifier,
                        "status": "error",
                    },
                )
                raise ConfigurationMissing(identifier=config_identifier) from None

        else:
            ctx.log_warning("...configuration missing!")
            ctx.record_warning(
                event="configuration.load",
                attributes={
                    "configuration": config.__qualname__,
                    "identifier": config_identifier,
                    "status": "missing",
                },
            )
            return None

    @overload
    @classmethod
    async def define(
        cls,
        config: Configuration,
        /,
        **extra: Any,
    ) -> None: ...

    @overload
    async def define(
        self,
        config: Configuration,
        /,
        **extra: Any,
    ) -> None:
        """Store a configuration instance using its class name as identifier.

        Args:
            config: Configuration instance to store.
            **extra: Additional parameters passed to the defining protocol.
        """
        ...

    @overload
    @classmethod
    async def define(
        cls,
        config: str,
        /,
        value: Configuration,
        **extra: Any,
    ) -> None: ...

    @overload
    async def define(
        self,
        config: str,
        /,
        value: Configuration,
        **extra: Any,
    ) -> None:
        """Store configuration data under a custom identifier.

        Args:
            config: Configuration identifier to store under.
            value: Configuration instance or raw data to store.
            **extra: Additional parameters passed to the defining protocol.
        """
        ...

    @statemethod
    async def define(
        self,
        config: Configuration | str,
        /,
        value: Configuration | None = None,
        **extra: Any,
    ) -> None:
        """Store configuration data in the repository.

        This method supports two usage patterns:
        1. Store a Configuration instance using its class name as identifier
        2. Store data under a custom identifier

        The configuration data is serialized to basic values before being
        passed to the defining protocol implementation.

        Args:
            config: Configuration instance or string identifier.
            value: Configuration data when using string identifier (ignored otherwise).
            **extra: Additional parameters passed to the defining protocol.

        Example:
            ```python
            # Store using class name as identifier
            db_config = DatabaseConfig(host="localhost", database="myapp")
            await ConfigurationRepository.define(db_config)

            # Store using custom identifier
            await ConfigurationRepository.define(
                "production_db",
                DatabaseConfig(host="prod.db.com", database="prod"),
            )
            ```
        """
        config_identifier: str
        config_value: Configuration
        if isinstance(config, str):
            config_identifier = config
            ctx.log_info(f"Defining configuration '{config_identifier}'...")

            assert value is not None  # nosec: B101
            config_value = value

        else:
            assert value is None  # nosec: B101
            config_identifier = config.__class__.__qualname__
            ctx.log_info(f"Defining configuration '{config_identifier}'...")
            config_value = config

        try:
            await self.defining(
                identifier=config_identifier,
                value=config_value,
                **extra,
            )

        except Exception as exc:
            ctx.log_error(
                f"...failed to define configuration '{config_identifier}'!",
                exception=exc,
            )
            ctx.record_error(
                event="configuration.define",
                attributes={
                    "identifier": config_identifier,
                    "status": "error",
                },
            )
            raise

        else:
            ctx.log_info("...configuration defined!")
            ctx.record_info(
                event="configuration.define",
                attributes={
                    "identifier": config_identifier,
                    "status": "success",
                },
            )

    @overload
    @classmethod
    async def remove[Config: Configuration](
        cls,
        identifier: type[Config] | str,
        /,
        **extra: Any,
    ) -> None: ...

    @overload
    async def remove[Config: Configuration](
        self,
        identifier: type[Config] | str,
        /,
        **extra: Any,
    ) -> None: ...

    @statemethod
    async def remove[Config: Configuration](
        self,
        identifier: type[Config] | str,
        /,
        **extra: Any,
    ) -> None:
        """Remove configuration data from the repository.

        This method removes configuration data associated with the given
        identifier from the storage backend.

        Args:
            identifier: Configuration class (uses __qualname__) or string identifier.
            **extra: Additional parameters passed to the removing protocol.

        Example:
            ```python
            # Remove by class
            await ConfigurationRepository.remove(DatabaseConfig)

            # Remove by string identifier
            await ConfigurationRepository.remove("production_db")
            ```
        """
        config_identifier: str
        if isinstance(identifier, str):
            config_identifier = identifier

        else:
            config_identifier = identifier.__qualname__

        ctx.log_info(f"Removing configuration '{config_identifier}'...")
        try:
            await self.removing(
                identifier=config_identifier,
                **extra,
            )

        except Exception as exc:
            ctx.log_error(
                f"...failed to remove configuration '{config_identifier}'!",
                exception=exc,
            )
            ctx.record_error(
                event="configuration.remove",
                attributes={
                    "identifier": config_identifier,
                    "status": "error",
                },
            )
            raise

        else:
            ctx.log_info("...configuration removed!")
            ctx.record_info(
                event="configuration.remove",
                attributes={
                    "identifier": config_identifier,
                    "status": "success",
                },
            )

    listing: ConfigurationListing = _empty_listing
    loading: ConfigurationLoading = _none_loading
    defining: ConfigurationDefining = _noop_defining
    removing: ConfigurationRemoving = _noop_removing
    meta: Meta = META_EMPTY
