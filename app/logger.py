"""Centralized logging configuration for the application."""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

def setup_logging(level: str = "INFO") -> None:
    """
    Set up logging configuration for the application.
    
    Args:
        level: The logging level to use. Defaults to "INFO".
    """
    # Create logs directory if it doesn't exist
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        
    # Set up formatting
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)
    
    # File handler (rotating, 10MB max per file, keep 5 backup files)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)
    
    # Set specific levels for some loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    # Log startup message
    root_logger.info("Logging system initialized")

def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger instance with the specified name.
    
    Args:
        name: The name for the logger. If None, returns the root logger.
    
    Returns:
        A configured logger instance.
    """
    return logging.getLogger(name)