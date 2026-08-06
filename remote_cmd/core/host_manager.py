"""
主机管理模块（向后兼容层）

保持原有 API 兼容，内部委托给新的 Repository + Service 层。

新的代码应直接使用:
    - remote_cmd.core.host.Host (代替 Host)
    - remote_cmd.service.host_service.HostService (代替 HostManager)
    - remote_cmd.repository.json_host_repository.JsonHostRepository
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# 向后兼容: Host 从新的 host.py 导出
from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import SSHClient
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

logger = logging.getLogger(__name__)


class HostManager:
    """
    主机管理器（向后兼容版本）

    保持原有 API 签名不变，内部委托给 HostService + JsonHostRepository。

    已弃用: 请使用 HostService + HostRepository 替代
    """

    def __init__(self, hosts_file: Optional[str] = None):
        """
        初始化主机管理器

        Args:
            hosts_file: 配置文件路径（可选）
        """
        self.hosts_file = hosts_file

        # 不指定文件时使用纯内存模式（保持向后兼容）
        if hosts_file:
            self._repo = JsonHostRepository(filepath=hosts_file, auto_load=True)
        else:
            self._repo = JsonHostRepository(filepath="hosts.json", auto_load=False)

        self._service = HostService(repository=self._repo)

        # 保持原有属性访问兼容
        self.hosts: dict[str, Host] = {}
        self._sync_hosts()

    def _sync_hosts(self):
        """同步 self.hosts 字典以保持向后兼容"""
        self.hosts = {h.name: h for h in self._repo.list()}

    # ========================================================================
    # 主机管理方法
    # ========================================================================

    def add_host(self, host: Host) -> None:
        self._service.add_host(host)
        self._sync_hosts()

    def update_host(self, name: str, **kwargs) -> Host:
        host = self._service.update_host(name, **kwargs)
        self._sync_hosts()
        return host

    def remove_host(self, name: str) -> None:
        self._service.remove_host(name)
        self._sync_hosts()

    def get_host(self, name: str) -> Host:
        return self._service.get_host(name)

    def list_hosts(self, tag: Optional[str] = None) -> list[Host]:
        return self._service.list_hosts(tag=tag)

    def list_tags(self) -> list[str]:
        return self._service.list_tags()

    # ========================================================================
    # 持久化方法
    # ========================================================================

    def save_to_file(self, filepath: str) -> None:
        """
        保存配置到文件

        注意: JsonHostRepository 使用 atomic write。
        如果 filepath 与初始化时的不同，会更新 repo 的路径。
        """
        from pathlib import Path

        current_path = str(self._repo._filepath)
        if Path(current_path) != Path(filepath):
            # 重新初始化 repo
            self._repo = JsonHostRepository(filepath=filepath, auto_load=False)
            self._repo.load_from_dict({h.name: h for h in self._service.list_hosts()})
        self._repo.flush()
        self._sync_hosts()

    def load_from_file(self, filepath: str) -> None:
        """从文件加载配置"""
        self._repo = JsonHostRepository(filepath=filepath, auto_load=True)
        self._service = HostService(repository=self._repo)
        self._sync_hosts()

    # ========================================================================
    # 连接测试方法
    # ========================================================================

    def connect_to_host(self, name: str) -> SSHClient:
        return self._service.connect_to_host(name)

    def test_connection(self, name: str) -> bool:
        """测试主机连接（通过 connect_to_host 保持 monkeypatch 兼容）"""
        try:
            with self.connect_to_host(name) as client:
                return client.is_connected()
        except OSError as e:
            logger.error(f"主机 {name} 连接测试失败: {e}")
            return False

    def test_all_connections(self, max_workers: int = 10) -> dict[str, bool]:
        """并行测试所有主机（通过 test_connection 保持 monkeypatch 兼容）"""
        results: dict[str, bool] = {}
        host_names = list(self.hosts.keys())

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.test_connection, name): name for name in host_names}
            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    results[name] = future.result()
                except Exception as e:  # noqa: BLE001
                    logger.error(f"主机 {name} 连接测试异常: {e}")
                    results[name] = False

        return results

    # ========================================================================
    # 魔术方法
    # ========================================================================

    def __enter__(self) -> "HostManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def __len__(self) -> int:
        return self._repo.count()

    def __contains__(self, name: str) -> bool:
        return self._repo.contains(name)
