#! /data/users/dqgu/anaconda3/bin/python
# coding=utf-8
import sys
import os

script_path = os.path.abspath(__file__)
if os.path.islink(script_path):
    script_path = os.readlink(script_path)
sys.path.append(os.path.dirname(os.path.dirname(script_path)))

from create_pipeline_template.batch_cmd_generator import NestedCmd
from nestpipe.workflow_basic import basic_arg_parser

# 初始化参数
parser = basic_arg_parser()
# 可以增加新的流程参数
parser.add_argument('-new_arg', required=False)

# 收集参数和记录命令行信息
args = parser.parse_args()
args.script_path = script_path

# 可以在这里增加对参数的判断
if args.pipeline_cfg is not None:
    print('')


# 从这里开始写pipeline, 有时一个步骤有两种选择, 请在这里自行判断

def pipeline():
    """
    注意：
    * 为了能正常跳过一些步骤,步骤名即step_name不能包含'_'
    * step_name不能重复, 保证最后生成的步骤名不能有重复**
    """
    nc = NestedCmd(args)
    nc.which_cmds()
    cmds = nc.which_cmds()
    nc.which2_cmds(depend_cmds=cmds)
    nc.run()


if __name__ == '__main__':
    pipeline()
