import logging
import os
import subprocess
import time
from typing import Optional

from src import meta
from src.model.message import (
    CompressionCurrentProgressMessage,
    CompressionErrorMessage,
    CompressionFinishedMessage,
    CompressionStartMessage,
    CompressionTotalProgressMessage,
)
from src.model.video import Task, VideoFile, is_progress_line, resolve_time_str
from src.service.config import ConfigService
from src.service.message import MessageService
from src.utils import timer


class VideoService:
    """
    视频处理服务类，提供视频压缩和处理的核心功能

    该类作为应用程序的核心服务之一，负责视频文件的压缩处理，支持单个文件处理和批量任务处理。
    它使用FFmpeg、x264、NeroAACEnc等工具实现视频压缩，并通过消息服务发送处理状态和进度信息，
    使UI能够实时更新处理进度。
    """

    _instance: Optional["VideoService"] = None

    running_process: list[subprocess.Popen] = []

    def __init__(self) -> None:
        if self._instance is not None:
            raise ValueError("VideoService 是单例类，不能重复实例化")

        self.message_service = MessageService.get_instance()

    @staticmethod
    def get_instance() -> "VideoService":
        """
        获取 VideoService 的单例实例

        Returns:
            VideoService: VideoService 的单例实例
        """
        if VideoService._instance is None:
            VideoService._instance = VideoService()

        return VideoService._instance

    @timer
    @staticmethod
    def process_single_file(
        file: VideoFile,
        config_name: str,
        delete_audio: bool,
        delete_source: bool,
    ):
        """
        处理单个视频文件的压缩任务

        Args:
            file: 视频文件对象，包含源文件路径和输出路径信息
            config_name: 压缩配置文件名，用于获取压缩参数
            delete_audio: 是否删除视频中的音频轨道
            delete_source: 是否在压缩完成后删除源文件

        Raises:
            ValueError: 当配置文件不存在或媒体信息读取错误时抛出
            subprocess.CalledProcessError: 当压缩命令执行失败时抛出
        """
        config_service = ConfigService.get_instance()

        # 读取配置
        config = config_service.get_config(config_name)
        if config is None:
            logging.error(f"配置文件 {config_name} 不存在")
            raise ValueError(f"配置文件 {config_name} 不存在")

        # Generate output filename
        output_path = file.output_path

        commands = []

        ffmpeg_path = meta.FFMPEG_PATH

        # Get preset string
        preset = config.x264.preset
        input_file = file.file_path

        if not delete_audio:
            # NOTE: -hwaccel 必须放在 -i 之前，它是 FFmpeg 的输入选项
            hwaccel_opt = "-hwaccel auto " if config.x264.opencl_acceleration else ""
            commands.append(
                f'"{ffmpeg_path}" -y {hwaccel_opt}-i "{input_file}" '
                + f"-c:v libx264 -crf {config.x264.crf} -preset {preset} "
                + f"-keyint_min {config.x264.I} -g {config.x264.I} "
                + f"-refs {config.x264.r} -bf {config.x264.b} "
                + "-me_method umh -sc_threshold 60 -b_strategy 1 -qcomp 0.5 -psy-rd 0.3:0 "
                + "-aq-mode 2 -aq-strength 0.8 "
                + "-c:a aac -b:a 128k "
                + "-movflags faststart "
                + f'-map 0 "{output_path}"'
            )
        else:
            # NOTE: -hwaccel 必须放在 -i 之前
            hwaccel_opt = "-hwaccel auto " if config.x264.opencl_acceleration else ""
            commands.append(
                f'"{ffmpeg_path}" -y {hwaccel_opt}-i "{input_file}" '
                + f"-c:v libx264 -crf {config.x264.crf} -preset {preset} "
                + f"-keyint_min {config.x264.I} -g {config.x264.I} "
                + f"-refs {config.x264.r} -bf {config.x264.b} "
                + "-me_method umh -sc_threshold 60 -b_strategy 1 -qcomp 0.5 -psy-rd 0.3:0 "
                + "-aq-mode 2 -aq-strength 0.8 "
                + "-an "
                + "-movflags faststart "
                + f'-map 0 "{output_path}"'
            )

        # Execute commands
        # total_commands = len(commands)
        for index, command in enumerate(commands):
            logging.info(f"执行命令: {command}")

            # 使用Popen创建子进程并添加到running_process列表
            process = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 合并stdout和stderr到stdout
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
            VideoService.running_process.append(process)

            # 等待进程完成，同时解析进度
            cur_time: float = -0.01  # 当前视频播放时间
            total_time: float = -1  # 视频总时长
            update_time = time.time()  # 上次更新进度的时间
            while process.poll() is None:
                line = ""
                try:
                    stdout = process.stdout
                    if not stdout:
                        continue

                    line = stdout.readline()

                    if not is_progress_line(line):
                        if total_time == -1 and "Duration" in line:
                            # 解析视频总时长
                            total_time = resolve_time_str(
                                line.split("Duration: ")[1].split(",")[0]
                            )
                            logging.debug(f"视频总时长: {total_time}")

                        if line.strip() == "":
                            continue

                        logging.debug(f"{line.strip()}")
                        continue

                    # 解析当前播放时间
                    cur_time = resolve_time_str(line.split("time=")[1].split(" ")[0])

                    # 发送进度
                    if update_time < time.time() - 1:
                        update_time = time.time()
                        MessageService.get_instance().send_message(
                            CompressionCurrentProgressMessage(
                                file_name=file.file_path,
                                current=cur_time,
                                total=total_time,
                            )
                        )

                except Exception as e:
                    logging.error(f"读取 stdout 时出错:  {e} 输出: {line.strip()}")

            stdout, stderr = process.communicate()

            # 从running_process列表中移除已完成的进程
            if process in VideoService.running_process:
                VideoService.running_process.remove(process)

            # Log command output
            if stdout:
                logging.debug(f"command stdout: {stdout.strip()}")
            if stderr:
                logging.warning(f"command stderr: {stderr.strip()}")

            # Check return code
            if process.returncode != 0:
                logging.error(f"命令执行失败，退出码: {process.returncode}")
                raise subprocess.CalledProcessError(process.returncode, command)

        # Delete source if requested
        if delete_source and os.path.exists(output_path):
            logging.debug(f"存在输出文件：{output_path}，删除源文件: {file.file_path}")
            os.remove(file.file_path)

    @timer
    @staticmethod
    def process_task(task: Task):
        """
        处理视频压缩任务，支持批量处理多个视频文件

        Args:
            task: 视频处理任务对象，包含待处理文件列表和处理配置

        该方法会：
        1. 发送任务开始消息
        2. 遍历处理任务中的每个视频文件
        3. 发送当前文件处理进度消息
        4. 调用process_single_file处理单个文件
        5. 处理可能出现的异常并发送错误消息
        6. 发送任务完成消息
        """
        message_service = MessageService.get_instance()

        logging.info(f"process task: {task.info}")

        logging.debug(f"process task sequence: {task.video_sequence}")

        if task.files_num == 0:
            message_service.send_message(
                CompressionErrorMessage("错误", "没有找到可处理的视频文件")
            )
            return

        message_service.send_message(CompressionStartMessage(task.files_num))

        # Process each file
        for index, video_file in enumerate(task.video_sequence, 1):
            logging.debug(
                f"process file: {video_file.file_path}, index: {index}, total: {task.files_num}"
            )

            # Notify start of processing
            message_service.send_message(
                CompressionTotalProgressMessage(
                    index - 1,
                    task.files_num,
                    video_file.file_path,
                )
            )

            try:
                VideoService.clean_temp_files()
                VideoService.process_single_file(
                    file=video_file,
                    config_name=task.info.process_config_name,
                    delete_audio=task.info.delete_audio,
                    delete_source=task.info.delete_source,
                )
            except Exception as e:
                logging.error(f"处理文件 {video_file.file_path} 失败: {e}")
                message_service.send_message(
                    CompressionErrorMessage(
                        "错误", f"处理文件 {video_file.file_path} 失败: {e}"
                    )
                )
            finally:
                VideoService.clean_temp_files()

        # Signal completion
        message_service.send_message(
            CompressionFinishedMessage(len(task.video_sequence))
        )

    @staticmethod
    def clean_temp_files():
        """
        清理视频处理过程中生成的临时文件

        该方法会遍历meta.TEMP_FILES中定义的所有临时文件路径，
        并删除存在的临时文件。如果删除失败，会记录警告日志但不会抛出异常。

        Returns:
            None
        """
        for temp_file in meta.TEMP_FILES:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    logging.warning(f"删除临时文件 {temp_file} 失败: {e}")

    @staticmethod
    def stop_process():
        """
        停止当前正在运行的视频处理进程

        该方法会终止running_process中存储的子进程，
        并等待其退出。如果进程未运行或已退出，
        则不执行任何操作。

        Returns:
            None
        """
        logging.info(
            f"正在停止所有视频处理进程，共 {len(VideoService.running_process)} 个进程"
        )

        # 创建进程列表的副本，避免在遍历过程中修改原列表
        processes_to_stop = list(VideoService.running_process)

        for process in processes_to_stop:
            try:
                logging.debug(f"正在终止进程: {process.pid}")
                process.terminate()

                # 等待进程退出，最多等待5秒
                logging.debug(f"等待进程 {process.pid} 退出")
                process.wait(timeout=5)

                if process.returncode is None:
                    # 如果进程仍未退出，强制终止
                    logging.warning(f"进程 {process.pid} 未在5秒内退出，正在强制终止")
                    process.kill()
                    # 再次等待确认进程退出
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        logging.error(f"进程 {process.pid} 无法强制终止")
                else:
                    logging.debug(
                        f"进程 {process.pid} 已退出，退出码: {process.returncode}"
                    )
            except Exception as e:
                logging.error(f"处理进程 {process.pid} 时发生错误: {e}")

        # 清空进程列表
        VideoService.running_process.clear()
        logging.info("所有视频处理进程已停止")

    @staticmethod
    def is_processing() -> bool:
        """
        检查是否有正在运行的视频处理进程

        Returns:
            bool: 如果有正在运行的进程则返回True，否则返回False
        """
        return len(VideoService.running_process) > 0
