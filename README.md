# desktop_and_mobilephone_imformation

用于多个手机和电脑在同一局域网下的通信

在作为服务器的电脑上运行dist文件中的.exe文件后，在需要通信的设备上运行connectx_x.html后，在作为服务器的电脑上唤出终端后输入ipconfig查找到IPv4的地址，然后将IP地址输入这个网站再点击链接即可

### connect1.1版本

加入了上传显示进度

加入了ip扫描能少输几个字

服务器终端会显示可能能连接的IP，不用专门去ipconfig了

能够做到完全的局域网的相互连接

一定要让作为服务器的电脑开热点让需要连接的设备连接这个热点

### mysql分支版本

mysql分支版本

支持永久文件保存

支持模糊搜索

支持网页直接访问，不需要html文件

使用方法：下载docker后运行命令

docker-compose up -d --build --remove-orphans

然后在浏览器输入自己的局域网IP地址就可以进入网址，然后输入局域网网址连接即可