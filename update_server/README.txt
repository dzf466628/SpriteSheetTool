更新服务器部署说明（服务器端零配置，不需要 PHP 脚本）
========================================================

原理
----
客户端（SpriteSheetTool v1.1.0+）启动时会请求：
    http://duadu.cc/download/SpriteSheetTool/.index.php

如果你的 Web 服务器（Apache/nginx）对该目录开启了“目录索引”（DirectoryIndex / autoindex），
这个地址返回的其实就是一个自动生成的目录列表页面（HTML）。
客户端会解析这个页面的文件名，自动挑出版本号最高的安装包，
与本地版本比较后决定是否提示“有新版本”。

版本号规则（每次打包）
--------------------
- 每次重新打包前，把版本号【最后一位 +1】：1.1.0 -> 1.1.1 -> 1.1.2 ...
- 需要同时改两处：
    spritesheet/__init__.py  里的 __version__
    installer.iss            里的 MyAppVersion
- 然后运行 build_windows.ps1 打包，得到 SpriteSheetTool_v.X.Y.Z.exe

你需要做的（每次发新版）
---------------------
1. 把新安装包上传到服务器目录：
       /download/SpriteSheetTool/SpriteSheetTool_v.1.1.1.exe
   （文件名必须符合 SpriteSheetTool_v.X.Y.Z.exe 的格式，这是客户端找版本的关键）

2. 确保目录索引开启（一般默认开启）：
   - Apache：目录里没有 index.html 时自动列出文件；或确认 httpd.conf 有
     Options Indexes
   - nginx：location 里加  autoindex on;

3. 完成。客户端下次启动就会自动发现新版本并提示下载。

注意
----
- 旧版本安装包可以保留在目录里（客户端只会挑版本最高的），也可以删掉。
- 如果希望强制 http 跳转到 https，服务器配置 301 即可，客户端会自动跟随重定向。
- 无需在服务器上放任何 PHP/JSON 文件，纯目录列表即可。
