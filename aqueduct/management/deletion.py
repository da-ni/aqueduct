from dataclasses import dataclass

from management.models import FileObject, ServiceAccount, Team, Token


@dataclass(frozen=True)
class Confirmation:
    """The question the confirm dialog, or the confirm page without JavaScript, asks before an
    action that cannot be undone."""

    title: str
    detail: str
    action: str = "Delete"


KEY_DATA = "requests, files, batches, and vector stores"


def deletion(obj: Token | ServiceAccount | Team | FileObject) -> Confirmation:
    """What deleting `obj` takes with it. The cascades of `Token`, `ServiceAccount`, `Team`,
    and `FileObject` decide each sentence."""
    match obj:
        case Token():
            name, detail = obj.name, f"Apps using it stop working, and its {KEY_DATA} are deleted."
        case ServiceAccount():
            name = obj.name
            detail = f"Its key stops working, and the key's {KEY_DATA} are deleted."
        case Team():
            name, count = obj.name, obj.service_accounts.count()
            detail = "Members keep their personal keys."
            if count == 1:
                gone = f"Its 1 service account is deleted with its key and the key's {KEY_DATA}."
                detail = f"{gone} {detail}"
            elif count:
                gone = f"Its {count} service accounts are deleted with their keys and the keys'"
                detail = f"{gone} {KEY_DATA}. {detail}"
        case FileObject():
            name = obj.filename
            detail = "Batches that use it as input are deleted with it, and vector stores drop it."
        case _:
            raise TypeError(f"no deletion text for {type(obj).__name__}")
    return Confirmation(f"Delete {name}?", detail)
