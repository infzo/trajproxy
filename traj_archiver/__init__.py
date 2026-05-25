"""
traj_archiver - 独立归档进程

将过期的请求详情分区归档到 S3 存储。
与核心业务 traj_proxy 完全独立，零依赖。
"""
