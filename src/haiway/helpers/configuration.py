from collections.abc import MutableMapping, Sequence
from typing import Any, Literal, Protocol, Self, overload, runtime_checkable

from haiway.attributes import State
from haiway.context import ctx
from haiway.helpers.statemethods import statemethod
from haiway.types import Meta

__all__ = (
    "Configuration",
    "ConfigurationDefining",
    "ConfigurationInvalid",
    "ConfigurationListing",
    "ConfigurationLoading",
    "ConfigurationMissing",
    "ConfigurationRemoving",
    "ConfigurationRepository",
)


class ConfigurationMissing(Exception):
    """
    Raised when a required configuration is not found.

    Raised by loading with ``required=True`` when the repository holds nothing for the
    identifier and the configuration can't be resolved from the context either.

    Attributes
    ----------
    identifier : str
        The configuration identifier which was not found.

    Examples
    --------
    >>> try:
    ...     config = await MyConfig.load(required=True)
    ... except ConfigurationMissing as exc:
    ...     ctx.log_error(f"Missing configuration: {exc.identifier}")
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
    """
    Raised when loading a configuration fails.

    The loading backend raised - the stored data could not be validated as the requested
    configuration class, or the backend failed to read it. The original error is chained as
    the cause.

    Attributes
    ----------
    identifier : str
        The configuration identifier which failed to load.
    reason : str
        The message of the original error.

    Examples
    --------
    >>> try:
    ...     config = await DatabaseConfig.load()
    ... except ConfigurationInvalid as exc:
    ...     ctx.log_error(f"Invalid configuration {exc.identifier}: {exc.reason}")
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
    """
    Base class for typed configuration objects.

    A configuration is a ``State`` loaded through the ``ConfigurationRepository`` of the
    current context - optionally, with a default, or as required.

    Examples
    --------
    >>> class DatabaseConfig(Configuration):
    ...     host: str = "localhost"
    ...     port: int = 5432
    ...     username: str
    ...     password: str
    ...
    >>> async with ctx.scope("app", ConfigurationRepository.volatile(stored_config)):
    ...     optional = await DatabaseConfig.load()  # None when not found
    ...     required = await DatabaseConfig.load(required=True)
    ...     fallback = await DatabaseConfig.load(
    ...         default=DatabaseConfig(username="admin", password="secret"),
    ...     )
    ...     named = await DatabaseConfig.load(identifier="production_db")
    """

    @overload
    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        **extra: Any,
    ) -> Self | None: ...

    @overload
    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        *,
        default: Self,
        **extra: Any,
    ) -> Self: ...

    @overload
    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        *,
        required: Literal[True],
        **extra: Any,
    ) -> Self: ...

    @classmethod
    async def load(
        cls,
        identifier: str | None = None,
        *,
        default: Self | None = None,
        required: bool = False,
        **extra: Any,
    ) -> Self | None:
        """
        Load the configuration from the repository of the current context.

        Delegates to ``ConfigurationRepository.load`` with this class.

        Parameters
        ----------
        identifier : str | None, default=None
            Identifier of the configuration, the ``__qualname__`` of the class when not given.
        default : Self | None, default=None
            Returned when the repository holds nothing for the identifier.
        required : bool, default=False
            Whether a missing configuration is an error - it is resolved from the context
            with ``ctx.state`` first, so a contextual instance, or one the class constructs
            without arguments, is used before ``ConfigurationMissing`` is raised.
        **extra : Any
            Additional parameters passed to the loading backend.

        Returns
        -------
        Self | None
            The loaded configuration, the default, or None when missing and not required.

        Raises
        ------
        ConfigurationMissing
            When required and neither the repository nor the context provides it.
        ConfigurationInvalid
            When the loading backend fails.
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


def _configuration(value: Any, /) -> Configuration:
    if isinstance(value, Configuration):
        return value

    raise TypeError(f"'{type(value).__name__}' is not a Configuration")


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
    """
    Repository of configurations, backed by replaceable functions.

    Each operation is a function the repository holds - listing, loading, defining and
    removing - so a backend is provided by passing its functions, and the repository is
    used through the context as any other state.

    Attributes
    ----------
    listing : ConfigurationListing
        Lists the identifiers of stored configurations - nothing by default.
    loading : ConfigurationLoading
        Loads a configuration as the requested class - None by default.
    defining : ConfigurationDefining
        Stores a configuration - does nothing by default.
    removing : ConfigurationRemoving
        Removes a configuration - does nothing by default.
    meta : Meta
        Metadata describing the repository.

    Examples
    --------
    >>> async def load_file[Config: Configuration](
    ...     config: type[Config],
    ...     identifier: str,
    ...     **extra: Any,
    ... ) -> Config | None:
    ...     path = Path("./configs") / f"{identifier}.json"
    ...     return config.from_json(path.read_text()) if path.exists() else None
    ...
    >>> async with ctx.scope("app", ConfigurationRepository(loading=load_file)):
    ...     config = await MyConfig.load(identifier="production")
    """

    @classmethod
    def volatile(
        cls,
        *configs: Configuration,
        **named_configs: Configuration,
    ) -> Self:
        """
        Create a repository keeping configurations in memory.

        Useful for tests, development and applications without persistent configuration.

        Parameters
        ----------
        *configs : Configuration
            Configurations stored under the ``__qualname__`` of their class.
        **named_configs : Configuration
            Configurations stored under the given identifiers.

        Returns
        -------
        Self
            Repository storing the configurations in memory.

        Raises
        ------
        TypeError
            When a given value is not a ``Configuration``.

        Examples
        --------
        >>> repository = ConfigurationRepository.volatile(
        ...     DatabaseConfig(host="localhost", database="app"),
        ...     production_db=DatabaseConfig(host="prod.db.com", database="prod"),
        ... )
        >>> async with ctx.scope("app", repository):
        ...     local = await DatabaseConfig.load()
        ...     production = await DatabaseConfig.load(identifier="production_db")
        """
        storage: MutableMapping[str, Configuration] = {}
        for element in configs:
            storage[type(_configuration(element)).__qualname__] = element
        for key, element in named_configs.items():
            storage[key] = _configuration(element)

        async def listing(
            config: type[Configuration] | None,
            **extra: Any,
        ) -> Sequence[str]:
            if config is None:
                return tuple(storage.keys())
            # the same configurations which loading accepts as the class
            return tuple(key for key, value in storage.items() if isinstance(value, config))

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
        """
        List the identifiers of stored configurations.

        Parameters
        ----------
        config : type[Configuration] | None, default=None
            Lists only the configurations of this class when given.
        **extra : Any
            Additional parameters passed to the listing backend.

        Returns
        -------
        Sequence[str]
            Identifiers of the stored configurations.
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
    ) -> Config | None: ...

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
    ) -> Config: ...

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
    ) -> Config: ...

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
        """
        Load a configuration as the given class.

        Parameters
        ----------
        config : type[Config]
            Class of the configuration to load.
        identifier : str | None, default=None
            Identifier of the configuration, the ``__qualname__`` of the class when not given.
        default : Config | None, default=None
            Returned when the repository holds nothing for the identifier.
        required : bool, default=False
            Whether a missing configuration is an error - it is resolved from the context
            with ``ctx.state`` first, so a contextual instance, or one the class constructs
            without arguments, is used before ``ConfigurationMissing`` is raised.
        **extra : Any
            Additional parameters passed to the loading backend.

        Returns
        -------
        Config | None
            The loaded configuration, the default, or None when missing and not required.

        Raises
        ------
        ConfigurationMissing
            When required and neither the repository nor the context provides it.
        ConfigurationInvalid
            When the loading backend fails, the original error chained as the cause.

        Examples
        --------
        >>> config = await ConfigurationRepository.load(DatabaseConfig)
        >>> named = await ConfigurationRepository.load(DatabaseConfig, identifier="production_db")
        >>> required = await ConfigurationRepository.load(DatabaseConfig, required=True)
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
    ) -> None: ...

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
    ) -> None: ...

    @statemethod
    async def define(
        self,
        config: Configuration | str,
        /,
        value: Configuration | None = None,
        **extra: Any,
    ) -> None:
        """
        Store a configuration in the repository.

        The configuration is passed to the defining backend as it is - stored under the
        ``__qualname__`` of its class, or under the identifier given before it.

        Parameters
        ----------
        config : Configuration | str
            The configuration to store, or the identifier to store ``value`` under.
        value : Configuration | None, default=None
            The configuration to store - required with an identifier, refused otherwise.
        **extra : Any
            Additional parameters passed to the defining backend.

        Raises
        ------
        TypeError
            When ``value`` is missing with an identifier, or given with a configuration.

        Examples
        --------
        >>> await ConfigurationRepository.define(DatabaseConfig(host="localhost", database="app"))
        >>> await ConfigurationRepository.define(
        ...     "production_db",
        ...     DatabaseConfig(host="prod.db.com", database="prod"),
        ... )
        """
        config_identifier: str
        config_value: Configuration
        if isinstance(config, str):
            config_identifier = config
            ctx.log_info(f"Defining configuration '{config_identifier}'...")
            if value is None:
                raise TypeError(f"Configuration '{config_identifier}' requires a value to define")

            config_value = value
        else:
            if value is not None:
                raise TypeError("A value can't be given together with a configuration to define")

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
        """
        Remove a configuration from the repository.

        Parameters
        ----------
        identifier : type[Config] | str
            Class of the configuration, removed by its ``__qualname__``, or its identifier.
        **extra : Any
            Additional parameters passed to the removing backend.

        Examples
        --------
        >>> await ConfigurationRepository.remove(DatabaseConfig)
        >>> await ConfigurationRepository.remove("production_db")
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
    meta: Meta = Meta.empty
