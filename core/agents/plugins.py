"""
Bridge between Hermes plugin system and stock_agent hooks system.

Discovers plugins in ~/.hermes/plugins/, reads their plugin.yaml hook declarations,
and registers them with the stock_agent hooks system (core/agents/hooks.py).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_LOADED = False


def load_plugins() -> None:
    """
    Discover and load all Hermes-style plugins from ~/.hermes/plugins/.
    Each plugin has a plugin.yaml declaring hooks.
    Hooks are registered with the stock_agent hooks system.
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    plugin_dir = Path.home() / ".hermes" / "plugins"
    if not plugin_dir.exists():
        logger.debug(f"Plugin dir not found: {plugin_dir}")
        return

    # ── 註冊 hermes_plugins namespace package ──
    # 為什麼：plugin 的 __init__.py 常用 `from . import xxx`（相對 import），
    # 需要父 package 存在於 sys.modules 才能正確錨定。原本沒做這層所以
    # disk-cleanup 等用相對 import 的 plugin 載入會失敗。
    if "hermes_plugins" not in sys.modules:
        import types as _types

        ns_pkg = _types.ModuleType("hermes_plugins")
        ns_pkg.__path__ = [str(plugin_dir)]  # namespace package（無 __init__.py）
        sys.modules["hermes_plugins"] = ns_pkg

    from core.agents.hooks import register_hook

    for plugin_path in plugin_dir.iterdir():
        if not plugin_path.is_dir():
            continue

        yaml_file = plugin_path / "plugin.yaml"
        init_file = plugin_path / "__init__.py"
        if not yaml_file.exists():
            continue

        try:
            import yaml

            with open(yaml_file) as f:
                config = yaml.safe_load(f)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"Failed to load plugin.yaml from {plugin_path}: {e}")
            continue

        plugin_name = config.get("name", plugin_path.name)
        raw_hooks = config.get("hooks", {})

        # stock_agent 的 plugin 約定：hooks 必須是 dict {hook_name: func_name}。
        # Hermes 原生 plugin 用 list 格式（透過 register(ctx) 註冊，與我們不同），
        # 直接跳過 — 那是 Hermes 自己的責任，不該載入也不該報 warning。
        if isinstance(raw_hooks, list):
            logger.debug(
                f"Plugin {plugin_name} uses Hermes list-style hooks "
                f"(incompatible with stock_agent loader); skipping."
            )
            continue
        if not isinstance(raw_hooks, dict):
            logger.debug(
                f"Plugin {plugin_name}: unexpected hooks type "
                f"{type(raw_hooks).__name__}; skipping."
            )
            continue
        hooks = raw_hooks

        # Plugin 目錄名稱可能含 dash（如 "disk-cleanup"），轉為合法 Python id
        safe_pkg_name = plugin_path.name.replace("-", "_")
        full_module_name = f"hermes_plugins.{safe_pkg_name}"

        # Register each declared hook
        for hook_name, hook_func_name in hooks.items():
            try:
                # Dynamically load the plugin module
                if str(plugin_path) not in sys.path:
                    sys.path.insert(0, str(plugin_path))

                # Reuse already-loaded module (multiple hooks per plugin)
                if full_module_name in sys.modules:
                    module = sys.modules[full_module_name]
                else:
                    spec = importlib.util.spec_from_file_location(
                        full_module_name,
                        init_file,
                        submodule_search_locations=[str(plugin_path)],
                    )
                    if spec is None or spec.loader is None:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    # 必須先註冊，再 exec — relative import 才找得到
                    sys.modules[full_module_name] = module
                    try:
                        spec.loader.exec_module(module)
                    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        # 載入失敗就回滾 sys.modules 註冊
                        sys.modules.pop(full_module_name, None)
                        raise

                hook_func = getattr(module, hook_func_name, None)
                if hook_func is None:
                    logger.warning(
                        f"Plugin {plugin_name} declares hook '{hook_name}' "
                        f"but function '{hook_func_name}' not found"
                    )
                    continue

                # Wrap the hook to convert ResponseContext → plugin format
                wrapped = _make_wrapper(hook_func, plugin_name, hook_name)
                register_hook(f"hermes:{plugin_name}:{hook_name}", wrapped)
                logger.info(f"Loaded Hermes plugin hook: {plugin_name}.{hook_name}")

            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(
                    f"Failed to load plugin hook {plugin_name}.{hook_name}: {e}"
                )


def _make_wrapper(hook_func: Callable, plugin_name: str, hook_name: str) -> Callable:
    """
    Wrap a Hermes plugin hook function so it accepts a ResponseContext
    and converts it to the format the plugin expects.

    Hermes plugin hooks expect keyword arguments like:
        session_id, user_message, assistant_response,
        conversation_history, model, platform
    """
    from core.agents.hooks import ResponseContext

    def wrapper(ctx: ResponseContext) -> None:
        try:
            # Build conversation_history from context metadata if available
            conversation_history = ctx.metadata.get("conversation_history", [])

            hook_func(
                session_id=ctx.session_id,
                user_message=ctx.query,
                assistant_response=ctx.response,
                conversation_history=conversation_history,
                model=ctx.metadata.get("model", ""),
                platform="discord",  # ctx.metadata.get("platform", "")
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"Hook {plugin_name}.{hook_name} failed: {e}")

    return wrapper
