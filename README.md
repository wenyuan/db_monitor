# DB Monitor
> 数据库监控。 </br>

## 环境
* Ubuntu 14.04
* Python 2.7

## oracle_monitor

### 1. 安装Oracle Instant Client(服务端)
* #### 安装RPM文件
在Oracle官网地址 [http://www.oracle.com/technetwork/topics/](http://www.oracle.com/technetwork/topics/linuxx86-64soft-092277.html) 下载以下RPM文件： </br>
其中官网说了： 18.3 can connect to Oracle Database 11.2 or later. </br>
所以我们就下载18.3版本好了。
```bash
oracle-instantclient18.3-basic-18.3.0.0.0-1.x86_64.rpm
oracle-instantclient18.3-devel-18.3.0.0.0-1.x86_64.rpm
oracle-instantclient18.3-sqlplus-18.3.0.0.0-1.x86_64.rpm
```
* 使用alien转换PRM文件到DEB文件并安装
```bash
sudo apt-get install alien
```
* 安装alien后，执行下面的命令安装Oracle客户端：
```bash
sudo alien -i oracle-instantclient18.3-basic-18.3.0.0.0-1.x86_64.rpm
sudo alien -i oracle-instantclient18.3-devel-18.3.0.0.0-1.x86_64.rpm
sudo alien -i oracle-instantclient18.3-sqlplus-18.3.0.0.0-1.x86_64.rpm
sudo apt-get install libaio1
```

### 2. 配置Oracle环境
* 按照以下步骤新增tnsnames.ora文件：
```bash
cd /usr/lib/oracle/18.3/client64/
sudo mkdir -p network/admin
cd network/admin/
sudo vim tnsnames.ora
```
* 填写如下内容，或者直接从oracle服务器端将相同目录下的这个文件拷贝过来。
```bash
# tnsnames.ora Network Configuration File
ORCL =
    (DESCRIPTION =
      (ADDRESS = (PROTOCOL = TCP)(HOST = 192.168.10.182)(PORT = 1521))
      (CONNECT_DATA =
        (SERVER = DEDICATED)
        (SERVICE_NAME = orcl)
      )
    )
```
* 链接Oracle的库文件到Oracle目录：
```bash
sudo ln -s /usr/include/oracle/18.3/client64 /usr/lib/oracle/18.3/client64/include
```

### 3. 配置环境变量
```bash
vim ~/.bashrc
```
在登录用户的profile中增加以下内容：
这里注意编码用UTF8，网上很多文章都写的是ZHS16GBK，不符合我们的一般习惯。
```bash
# oracle instant client
export ORACLE_HOME=/usr/lib/oracle/18.3/client64
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$ORACLE_HOME/lib
export TNS_ADMIN=$ORACLE_HOME/network/admin
export PATH=$PATH:$ORACLE_HOME/bin
export NLS_LANG="SIMPLIFIED CHINESE_CHINA.UTF8"
```
使当前用户的profile文件生效
```bash
source ~/.profile
```

### 4. 添加文件
```bash
sudo vim /etc/ld.so.conf.d/oracle.conf
```
加入以下内容：
```bash
/usr/lib/oracle/18.3/client64/lib/
```
然后执行命名
```bash
sudo ldconfig
```

### 5. 安装cx_Oracle，测试Python连接数据库
```bash
pip install cx_Oracle==7.0.0
```
用如下代码进行测试：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
import cx_Oracle

if __name__ == "__main__":
    conn = cx_Oracle.connect('system', 'password', '192.168.10.182:1521/ORCL')
    print(conn.version)
    conn.close()
```
