from __future__ import annotations

from .types import TargetHandle, TargetOwnerTag


class LocalTargetProvider:
    """Stateless local target adapter.

    TargetEpoch authority lives in the KernelLoop store. The provider receives
    the persisted epoch and never invents/reset it in private memory.
    """

    async def acquire(
        self,
        *,
        run_id: str,
        task_id: str,
        epoch: int,
    ) -> TargetHandle:
        instance_id = f"local-{task_id}"
        return TargetHandle(
            instance_id=instance_id,
            epoch=epoch,
            endpoint=None,
            owner=TargetOwnerTag(run_id, task_id, instance_id, epoch),
        )

    async def reset(self, handle: TargetHandle, *, new_epoch: int) -> TargetHandle:
        if new_epoch <= handle.epoch:
            raise ValueError("target epoch must increase")
        return TargetHandle(
            instance_id=handle.instance_id,
            epoch=new_epoch,
            endpoint=handle.endpoint,
            owner=TargetOwnerTag(
                handle.owner.run_id,
                handle.owner.task_id,
                handle.instance_id,
                new_epoch,
            ),
        )

    async def reconcile(self, handle: TargetHandle, *, authoritative_epoch: int) -> str:
        return "usable" if handle.epoch == authoritative_epoch else "stale"
