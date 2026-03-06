"""账号存储"""
import json
import os
from pathlib import Path
from typing import List, Optional
from distribute.models import Account


class AccountStore:
    """账号存储管理"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.file_path = self.data_dir / "accounts.json"

    def _load(self) -> List[dict]:
        if not self.file_path.exists():
            return []
        with open(self.file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save(self, accounts: List[dict]):
        tmp_path = self.file_path.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.file_path)

    def add(self, account: Account) -> Account:
        accounts = self._load()
        account_dict = {
            'platform': account.platform,
            'account_id': account.account_id,
            'account_name': account.account_name,
            'cookies_path': account.cookies_path,
            'enabled': account.enabled,
            'metadata': account.metadata
        }
        accounts.append(account_dict)
        self._save(accounts)
        return account

    def list_all(self) -> List[Account]:
        accounts = self._load()
        return [Account(**acc) for acc in accounts]

    def delete(self, platform: str, account_id: str) -> bool:
        accounts = self._load()
        filtered = [a for a in accounts if not (a['platform'] == platform and a['account_id'] == account_id)]
        if len(filtered) == len(accounts):
            return False
        self._save(filtered)
        return True
