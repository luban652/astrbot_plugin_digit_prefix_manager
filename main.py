import re
import time
import json
from pathlib import Path
from typing import List
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# 尝试导入特定平台的事件类型以进行更安全的类型检查
try:
    from astrbot.api.platform.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
except ImportError:
    AiocqhttpMessageEvent = None

@register("astrbot_plugin_digit_replace_manager", "AstrBotAssistant", "检测群聊中独立的5位数字并替换群名中任意位置的数字，支持自动截断超长群名，并提供白名单模式及'清空'指令。", "1.0.1")
class DigitReplaceManager(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config_path = Path(__file__).parent / "config.json"
        self.config = self._load_config()
        self._merge_default_config()
        self.digit_pattern = re.compile(r"^\d{5}$")
        self.digit_find_pattern = re.compile(r"\d{5}")
        self.cooldown_cache = {}
        self.cooldown_seconds = 3
        logger.info("=== 数字替换插件已加载 ===")

    def _load_config(self):
        default_config = {
            "global_enabled": False,
            "whitelist": [],
            "max_length": 30,
            "enable_notify": True,
            "admin_only": False,
            "ignore_patterns": []
        }
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    for key, value in default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
                return default_config
        return default_config

    def _merge_default_config(self):
        default_config = {
            "global_enabled": False,
            "whitelist": [],
            "max_length": 30,
            "enable_notify": True,
            "admin_only": False,
            "ignore_patterns": []
        }
        for key, value in default_config.items():
            if key not in self.config:
                self.config[key] = value

    def _save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            logger.info(f"配置已保存: {self.config_path}")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def _is_enabled(self, group_id: str) -> bool:
        if self.config.get("global_enabled", False):
            return True
        whitelist = self.config.get("whitelist", [])
        return str(group_id) in whitelist

    def _check_cooldown(self, group_id: str) -> bool:
        now = time.time()
        last_time = self.cooldown_cache.get(group_id, 0)
        if now - last_time < self.cooldown_seconds:
            return False
        self.cooldown_cache[group_id] = now
        return True

    @filter.command("开启改名")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def enable_digit_replace(self, event: AstrMessageEvent):
        """开启当前群的数字替换功能"""
        group_id = event.message_obj.group_id
        if not group_id:
            yield event.plain_result("仅限群聊")
            return

        group_id_str = str(group_id)
        current_whitelist = list(self.config.get("whitelist", []))
        
        if group_id_str not in current_whitelist:
            current_whitelist.append(group_id_str)
            self.config["whitelist"] = current_whitelist
            self._save_config()
            yield event.plain_result(f"已开启")
            logger.info(f"群 {group_id_str} 已加入白名单")
        else:
            yield event.plain_result(f"已开启")

    @filter.command("关闭改名")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def disable_digit_replace(self, event: AstrMessageEvent):
        """关闭当前群的数字替换功能"""
        group_id = event.message_obj.group_id
        if not group_id:
            yield event.plain_result("仅限群聊")
            return

        group_id_str = str(group_id)
        current_whitelist = list(self.config.get("whitelist", []))
        
        if group_id_str in current_whitelist:
            current_whitelist.remove(group_id_str)
            self.config["whitelist"] = current_whitelist
            self._save_config()
            yield event.plain_result(f"已关闭")
            logger.info(f"群 {group_id_str} 已移出白名单")
        else:
            yield event.plain_result(f"已关闭")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def handle_group_message(self, event: AstrMessageEvent):
        """核心逻辑：监听群消息并处理数字替换"""
        msg_str = event.message_str.strip()
        
        # 提前判断是否需要处理
        if not (self.digit_pattern.match(msg_str) or msg_str == "清空"):
            return
        
        # 立即停止事件传播，防止被其他插件拦截
        event.stop_event()
        
        group_id = event.message_obj.group_id
        if not group_id:
            return
        
        group_id_str = str(group_id)
        logger.info(f"收到数字替换请求: {msg_str}, 群: {group_id_str}")
        
        # 检查是否启用
        if not self._is_enabled(group_id_str):
            logger.info(f"群 {group_id_str} 未启用")
            return

        # 检查权限
        if self.config.get("admin_only", False):
            if event.get_permission_level() < filter.PermissionType.ADMIN:
                return

        if msg_str == "清空":
            await self._modify_group_name(event, clear=True)
            return

        if self.digit_pattern.match(msg_str):
            if msg_str in self.config.get("ignore_patterns", []):
                return
            await self._modify_group_name(event, new_digit=msg_str)

    async def _modify_group_name(self, event: AstrMessageEvent, new_digit: str = None, clear: bool = False):
        is_cqhttp = False
        if AiocqhttpMessageEvent and isinstance(event, AiocqhttpMessageEvent):
            is_cqhttp = True
        elif event.get_platform_name() == "aiocqhttp":
            is_cqhttp = True

        if not is_cqhttp:
            logger.warning("不支持当前平台")
            return

        group_id = str(event.message_obj.group_id)
        
        if not self._check_cooldown(group_id):
            if self.config.get("enable_notify", True):
                await event.send(event.plain_result("操作过于频繁，请稍后再试"))
            return

        try:
            client = event.bot
            int_group_id = int(group_id)
            group_info = await client.api.call_action('get_group_info', group_id=int_group_id)
            if not group_info or 'group_name' not in group_info:
                logger.error(f"无法获取群 {group_id} 的信息")
                return
            
            current_name = group_info['group_name']
            logger.info(f"当前群名: {current_name}")
            
            if clear:
                # 清空模式：移除所有5位数字
                target_name = self.digit_find_pattern.sub("", current_name).strip()
                # 清理可能产生的多余空格
                target_name = re.sub(r'\s+', ' ', target_name).strip()
                target_name = target_name.strip()
                logger.info(f"清空后群名: {target_name}")
            else:
                # 替换模式：找到第一个5位数字并替换
                match = self.digit_find_pattern.search(current_name)
                if match:
                    start = match.start()
                    end = match.end()
                    matched_text = current_name[start:end]
                    logger.info(f"找到匹配: {matched_text}，位置: {start}-{end}")
                    target_name = current_name[:start] + new_digit + current_name[end:]
                    logger.info(f"替换后群名: {target_name}")
                else:
                    # 如果没有5位数字，在开头添加
                    target_name = f"{new_digit}{current_name}" if current_name else new_digit
                    logger.info(f"无匹配，添加前缀: {target_name}")
            
            # 长度限制
            max_len = self.config.get("max_length", 30)
            if len(target_name) > max_len:
                target_name = target_name[:max_len]
                logger.info(f"截断后群名: {target_name}")

            if target_name == current_name:
                logger.info("群名未变化，跳过修改")
                return

            # 执行修改
            await client.api.call_action('set_group_name', group_id=int_group_id, group_name=target_name)
            
            if self.config.get("enable_notify", True):
                if clear:
                    await event.send(event.plain_result(""))
                else:
                    await event.send(event.plain_result(f""))
                    
        except ValueError:
            logger.error(f"非法的群组 ID: {group_id}")
        except Exception as e:
            logger.error(f"修改群名失败: {str(e)}")
            if self.config.get("enable_notify", True):
                await event.send(event.plain_result("修改失败，请检查机器人权限"))

    async def terminate(self):
        pass
