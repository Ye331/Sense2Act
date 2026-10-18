"""新闻检测、内部信号与公共任务提交。"""
from .service import NewsService
from .signal_detector import NewsSignalDiscovery
from .submission import news_to_submission

__all__ = ['NewsService', 'NewsSignalDiscovery', 'news_to_submission']
