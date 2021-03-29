#基于的基础镜像
FROM python:3.8-slim
#代码添加到code文件夹，后面可以通过进入容器中看的
ADD ./ /basic
# 设置code文件夹是工作目录
WORKDIR /basic
# 安装graphviz
RUN apt-get update && apt-get install -y gcc gsfonts libgraphviz-dev graphviz-doc libgd-tools graphviz python-pygraphviz
# 安装python包
RUN pip install -r requirements.txt
#当容器启动时，使用python3执行指定路径的py脚本
CMD ["echo", "usage: docker run -it <image_id> python3 nestcmd.py -h "]
