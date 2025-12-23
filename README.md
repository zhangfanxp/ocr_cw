1、拉取项目文件

https://github.com/zhangfanxp/ocr_cw.git

2、执行数据库创建脚本

mysql -u root -p < create_db.sql

参考你本地的了mysql数据库账号,密码,对应修改app.py中相应的配置信息(默认是root,Abs)*m12d31)

3、创建并激活虚拟环境

uv venv && source .venv/bin/activate

4、安装依赖库

uv pip install -r requirements.txt

5、运行服务程序

python app.py


------------------------------------------------------------------------------------------------------------------

邮箱账号(授权码)和LLM的API Key都可以通过设置来进行配置,也可以直接修改目录下的mail_account.json和LLM_Api_Key.json两个json文件.
