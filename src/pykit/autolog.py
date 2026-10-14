import dataclasses
import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from inspect import isfunction
from typing import Any, Literal, Protocol, TypeVar, overload
from weakref import WeakValueDictionary

from wpiutil import wpistruct

from pykit.logtable import LogTable
from pykit.logvalue import LogValue

C = TypeVar("C", bound=type[Any])
F = TypeVar("F", bound=Callable[..., Any])


class AutologgedDataclass(Protocol):
    def toLog(self, table: LogTable, prefix: str): ...
    def fromLog(self, table: LogTable, prefix: str): ...


LogTypeTuple = tuple[
    type[Any] | Literal["wpistruct"], type[Any] | Literal["wpistruct"] | None
]
_table_get_dispatch: dict[
    LogTypeTuple,
    Callable[[LogTable, type[Any], str, Any], Any],
] = {
    (bool, None): lambda table, field_type, field_prefix, value: table.getBoolean(
        field_prefix, value
    ),
    (list, bool): lambda table, field_type, field_prefix, value: table.getBooleanArray(
        field_prefix, value
    ),
    (float, None): lambda table, field_type, field_prefix, value: table.getDouble(
        field_prefix, value
    ),
    (list, float): lambda table, field_type, field_prefix, value: table.getDoubleArray(
        field_prefix, value
    ),
    (int, None): lambda table, field_type, field_prefix, value: table.getInteger(
        field_prefix, value
    ),
    (list, int): lambda table, field_type, field_prefix, value: table.getIntegerArray(
        field_prefix, value
    ),
    (str, None): lambda table, field_type, field_prefix, value: table.getString(
        field_prefix, value
    ),
    (list, str): lambda table, field_type, field_prefix, value: table.getStringArray(
        field_prefix, value
    ),
    ("wpistruct", None): lambda table, field_type, field_prefix, value: (
        wpistruct.unpack(field_type, table.getRaw(field_prefix, b""))
    ),
    (list, "wpistruct"): lambda table, field_type, field_prefix, value: (
        wpistruct.unpackArray(field_type, table.getRaw(field_prefix, b""))
    ),
}


@dataclass
class InstanceMetadata:
    key: str
    is_method: bool
    log_type: LogValue.LoggableType | None = None
    name: str = ""
    custom_type: str = ""
    unit: str | None = None


@dataclass
class ClassRegistry:
    member_data: list[InstanceMetadata] = field(default_factory=list)
    instances: WeakValueDictionary[int, Any] = WeakValueDictionary()


_registry: dict[type[Any], ClassRegistry] = {}


def _register_instance(instance: Any, cls: type[Any], name: str) -> None:
    class_registry = _registry[cls]
    ident = id(instance)

    print(f"[AutoLog] registering {name}, id:{ident}")
    class_registry.instances[ident] = instance


def _register_class(cls: type[Any], name: str):
    """Registers a class for automatic input loading.

    :param class_to_register: The class type to register.

    """
    _registry.setdefault(cls, ClassRegistry())

    # Invisibly modify __init__ so it also adds itself to the registry
    original_init = cls.__init__

    @wraps(original_init)
    def init_and_register(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _register_instance(self, cls, name)

    cls.__init__ = init_and_register
    return cls


def _publish_all(table: LogTable, subtable: LogTable, isReplay: bool) -> None:
    seen: set[int] = set()

    for class_registry in tuple(_registry.values()):
        for instance in list(class_registry.instances.values()):
            ident = id(instance)
            if ident in seen:
                continue
            seen.add(ident)
            _publish(instance, table, subtable, isReplay)


def _register_member(class_type: type[Any], info: InstanceMetadata):
    """Registers a member (field or method) of a class for automatic output logging.

    :param class_type: The class to which the member belongs.
    :param member_name: The name of the member (field or method).
    :param is_method: True if the member is a method, False otherwise.
    :param log_type: The `LogValue.LoggableType` to log the member as.
    :param key: The key to use for logging. Defaults to the member name.
    :param custom_type: A custom type string for the log entry.
    :param unit: The unit string for the log entry.

    """
    if class_type not in _registry:
        return

    _registry[class_type].member_data.append(
        InstanceMetadata(
            name=info.name,
            is_method=info.is_method,
            log_type=info.log_type,
            key=info.key,
            custom_type=info.custom_type,
            unit=info.unit,
        )
    )


def _publish(instance: Any, table: LogTable, subtable: LogTable, isReplay: bool):
    """Publishes the values of all registered members of an instance to a LogTable.

    :param instance: The instance whose members are to be published.
    :param table: The LogTable to publish data to.

    """
    class_type = type(instance)
    if class_type in _registry:
        if dataclasses.is_dataclass(class_type) and not isReplay:
            typing.cast(AutologgedDataclass, instance).toLog(
                subtable, f"/{instance.__class__.__name__}"
            )

        for member_info in _registry[class_type].member_data:
            member_name = member_info.name
            is_method = member_info.is_method
            log_type = member_info.log_type
            custom_type = member_info.custom_type
            unit = member_info.unit

            key = member_info.key or member_name

            # Get the value from the instance (call if method, access if field)
            value = (
                getattr(instance, member_name)()
                if is_method
                else getattr(instance, member_name)
            )

            # Handle WPILib struct types specially
            if hasattr(value, "WPIStruct") or (
                hasattr(value, "__iter__")
                and len(value) > 0
                and hasattr(value[0], "WPIStruct")
            ):
                table.put(key, value)
            else:
                # Wrap value in LogValue and override type if specified
                log_value = LogValue(value, custom_type, unit)
                if log_type is not None:
                    log_value.log_type = log_type
                table.putValue(key, log_value)


def _resolve_method(member: Any) -> Callable[..., Any] | None:
    if isinstance(member, (staticmethod, classmethod)):
        return member.__func__

    elif isinstance(member, property):
        return member.fget

    elif isfunction(member):
        return member

    else:
        return None


_pending_members: dict[Any, InstanceMetadata] = {}


def _decorate_method(
    key: str,
    log_type: LogValue.LoggableType | None = None,
    custom_type: str = "",
    unit: str | None = None,
):
    """
    A decorator for methods or fields in a class to automatically log their output.

    Usage:
        @autologgable_output
        class MyComponent:
            my_value = 5

            @autolog_output("MyValue")
            def get_my_value(self):
                return self.my_value

    :param key: The key to use for logging the member's value.
    :param log_type:
        The `LogValue.LoggableType` to log the value as. If None, it's inferred.
    :param custom_type: A custom type string for the log entry.
    :param unit: The unit string for the log entry.
    :return: A decorator function.
    """

    def decorate(member: Any):
        # This part is tricky because Python decorators for methods/fields
        # don't directly give you the class at definition time.
        # We'll store a temporary attribute and process it in a class decorator.
        print(f"[AutoLogOutput] DEBUG: Setting up log for {key}")

        _pending_members[member] = InstanceMetadata(
            name=member.__name__,
            is_method=not isinstance(member, property),
            log_type=log_type,
            custom_type=custom_type,
            key=key,
            unit=unit,
        )
        return member

    return decorate


def _get_dispatch_type(field_type) -> tuple[LogTypeTuple, type[Any]]:
    is_wpistruct = hasattr(field_type, "WPIStruct")
    if typing.get_origin(field_type) is list:
        list_type = typing.get_args(field_type)[0]
        return (list, "wpistruct" if is_wpistruct else list_type), list_type

    return ("wpistruct" if is_wpistruct else field_type, None), field_type


def _autolog_dataclass(cls=None, /):
    """A class decorator that adds 'toLog' and 'fromLog' methods to a dataclass for
    automatic logging.

    The 'toLog' method serializes the dataclass fields to a LogTable. The 'fromLog'
    method deserializes the data from a LogTable into the dataclass fields.

    This decorator is designed to be used with dataclasses and supports nested
    dataclasses decorated with @autolog.

    :param cls: The class to decorate.
    :return: The decorated class or a wrapper function.

    """

    def wrap(clS):
        resolved_hints = typing.get_type_hints(clS)
        field_names = [field.name for field in dataclasses.fields(clS)]

        def toLog(self, table: LogTable, prefix: str):
            """Recursively logs the fields of the dataclass to a LogTable.

            :param table: The LogTable instance to write to.
            :param prefix: The prefix for the log entries.

            """
            for name in field_names:
                value = getattr(self, name)
                field_prefix = f"{prefix}/{name}"
                if hasattr(value, "toLog"):
                    value.toLog(table, field_prefix)
                else:
                    table.put(field_prefix, value)

        def fromLog(self, table: LogTable, prefix: str):
            """Recursively reads the fields of the dataclass from a LogTable.

            :param table: The LogTable instance to read from.
            :param prefix: The prefix for the log entries.

            """
            for name in field_names:
                field_prefix = f"{prefix}/{name}"

                value = getattr(self, name)
                # Recursively load nested autolog dataclasses
                if hasattr(value, "fromLog"):
                    value.fromLog(table, field_prefix)
                    continue
                # Load primitive types and arrays from log table
                dispatch_type, elem_type = _get_dispatch_type(resolved_hints[name])
                new_value: Any = None

                if (getter := _table_get_dispatch.get(dispatch_type, None)) is not None:
                    if (
                        new_value := getter(table, elem_type, field_prefix, value)
                    ) is not None:
                        setattr(self, name, new_value)

                    continue

                if dispatch_type[0] == "wpistruct":
                    print(f"[AutoLog] Failed to read of type {elem_type} (wpistruct)")
                elif dispatch_type[1] == "wpistruct":
                    print(
                        "[AutoLog] Failed to read of type list with element type "
                        f"{elem_type} (wpistruct)"
                    )
                else:
                    print(
                        f"[AutoLog] Failed to read of type {dispatch_type[0]}"
                        f"{
                            f' with value {elem_type}'
                            if dispatch_type[0] is list
                            else ''
                        }"
                    )

        setattr(clS, "toLog", toLog)
        setattr(clS, "fromLog", fromLog)

        return clS

    if cls is None:
        return wrap

    return wrap(cls)


@overload
def autolog(target: C, /, *, name: str) -> C: ...


@overload
def autolog(target: F, /, *, name: str) -> F: ...


@overload
def autolog(
    target: None = None,
    /,
    *,
    name: str,
    key: str | None,
    log_type: LogValue.LoggableType | None = None,
    custom_type: str = "",
    unit: str | None = None,
) -> Callable[[F], F]: ...


def _autolog_class(cls: type[Any], name: str):
    _register_class(cls, name)

    for name, member in vars(cls).items():
        if (resolved_method := _resolve_method(member)) is None:
            continue

        if (metadata := _pending_members.pop(resolved_method, None)) is not None:
            _register_member(
                cls,
                dataclasses.replace(
                    metadata, name=name, is_method=not isinstance(member, property)
                ),
            )

    if dataclasses.is_dataclass(cls):
        _autolog_dataclass(cls)

    return cls


def autolog(
    target: Any = None,
    /,
    *,
    name: str | None,
    key: str | None = None,
    log_type: LogValue.LoggableType | None = None,
    custom_type: str = "",
    unit: str | None = None,
) -> Any:
    def decorate(obj: Any) -> Any:
        if inspect.isclass(obj):
            if name is None:
                raise ValueError("A name is required for autologged classes.")
            return _autolog_class(obj, name)
        elif inspect.isfunction(obj):
            if name is not None:
                print("[Autolog] DEBUG: Names are ignored for autologged methods.")
            return _decorate_method(
                key=key or obj.__name__,
                log_type=log_type,
                custom_type=custom_type,
                unit=unit,
            )(obj)

        else:
            raise TypeError(f"@autolog does not support {type(obj)}")

    return decorate if target is None else decorate(target)


def autologged_dataclass(
    cls: type[Any],
    slots: bool = False,
    /,
    *,
    name: str,
    key: str | None = None,
    log_type: LogValue.LoggableType | None = None,
    custom_type: str = "",
    unit: str | None = None,
):
    return autolog(
        dataclass(cls, slots=slots, weakref_slot=slots),  # type: ignore
        name=name,
        key=key,
        log_type=log_type,
        custom_type=custom_type,
        unit=unit,
    )
