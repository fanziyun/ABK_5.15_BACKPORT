# erofs 压缩前提核查 — 真机（vermeer / 5.15.216）

日期 **2026-09-22**，无线 adb（mDNS `adb-14d93093-Owcuqs` → `192.168.31.20:40909`），
KernelSU root，内核 `5.15.216-202609202-FanZiyun-pr22-41b7b66`（= PR #22 的构建）。
目的：Batch 40（erofs 预读解压临时缓冲放开，上游 `d9281660ff3f`）的**前提是否成立**。
本仓先例：Batch 38 的真机核查纠正过两处错误前提，故本批同样先核前提再注册组。

## 结论：前提成立 —— 挂载的 erofs **确实带 LZ4 压缩**，且解压路径在真机上是活的

**方法**：superblock 的 `feature_incompat` 单独不足以判定（`LZ4_0PADDING` 与
`lz4_max_distance` 的默认值会让未压缩镜像看起来一样），所以做了**逐 inode 的判定**：
从挂载的 `/dev/block/dm-N` 拉 16 MB 头部，按 5.15 的 on-disk 格式走目录树，
取真正被目录项引用到的普通文件 inode，读它的 `i_format` datalayout。

**决定性证据（目录树走查 + inode datalayout）**：

| 镜像 | 文件 | nid | i_size | datalayout |
|---|---|---|---|---|
| `dm-1` = `/` | `init.environ.rc` | 135 | 621 | `FLAT_INLINE`（2，未压缩） |
| `dm-1` = `/` | （子目录内） | 640 | 12880 | **`FLAT_COMPRESSION`（3）**，`compressed_blocks=2` |
| `dm-3` = `/vendor` | `build.prop` | 110 | 16314 | **`FLAT_COMPRESSION`（3）**，`compressed_blocks=2` |

`/vendor/build.prop` 16314 B 压进 2 个块（8192 B）≈ 2:1，正是 LZ4 在 prop 类文本上的典型
比值。`EROFS_INODE_FLAT_COMPRESSION` 的语义见 `fs/erofs/erofs_fs.h:95`，由
`erofs_inode_is_data_compressed()`（同文件 :102）判定为压缩。

目录走查的正确性自证：`/` 的目录项逐条还原出 Android 真实根目录
（`adb_keys apex bin bootstrap-apex config cust data … opconfig opcust postinstall proc
product sdcard second_stage_resources storage sys system system_dlkm system_ext tmp
vendor`），`/vendor` 还原出 `app bin bt_firmware build.prop dsp etc firmware
firmware_mnt framework gpu lib lib64 odm odm_dlkm overlay rfs`。所以取到的 nid 不是误报。

### 5.15 的 on-disk 格式要点（本次核实，供后续 erofs 工作复用）

- `struct erofs_dirent` 在 5.15 是 **12 字节**（`__le64 nid; __le16 nameoff; __u8 file_type;
  __u8 reserved;`），不是上游后来的 16 字节（上游把 `reserved` 扩成 `reserved[5]`）。
  按 16 字节走会得到一堆看似「垃圾 nid」的错位，这是本次第一个坑。
- `nameoff` 相对**目录数据块起点**（不是相对目录项本身）；每块的 `maxsize` = 第一个目录项的
  `nameoff`，目录项数组占 `[0, maxsize)`，文件名区占 `[maxsize, blocksize)`，各名字**正序**连续、
  无 NUL 终止（长度由后一项的 `nameoff` 之差给出）。
- `struct erofs_xattr_ibody_header` 是 **12 字节**，`erofs_xattr_ibody_size() = 12 + 4*(icount-1)`
  （`icount` 为 0 时整体为 0）。inline 目录数据的起点 = inode 偏移 + 32 + 该值；
  算成 4 字节会整体错位 4 字节（本次第二个坑）。
- `EROFS_INODE_FLAT_COMPRESSION = 3`、`_LEGACY = 1`、`FLAT_INLINE = 2`、`FLAT_PLAIN = 0`，
  位域是 `i_format` 的 `[1,4)`（`EROFS_I_DATALAYOUT_BIT 1`，`_BITS 3`）。

## 挂载与配置事实

`/proc/self/mountinfo`：**所有系统分区都是 erofs**，且挂载选项带
`cache_strategy=readaround`（Xiaomi fstab 对压缩 erofs 的典型选项）：

```
42 41 253:1 / /          ro,relatime shared:1 - erofs /dev/block/dm-1 ro,seclabel,user_xattr,acl,cache_strategy=readaround
53 42 253:2 / /system_ext ... erofs /dev/block/dm-2
54 42 253:0 / /product    ... erofs /dev/block/dm-0
55 42 253:3 / /vendor     ... erofs /dev/block/dm-3
56 42 253:4 / /vendor_dlkm ... erofs /dev/block/dm-4
57 42 253:7 / /system_dlkm ... erofs /dev/block/dm-7
58 42 253:6 / /odm        ... erofs /dev/block/dm-6
49 48 253:5 / /mnt/vendor/mi_ext ... erofs /dev/block/dm-5
```
另有大量 APEX 的 loop 挂载同为 erofs。

`/proc/config.gz`（本批相关行，逐字抄录）：

```
CONFIG_EROFS_FS=y
# CONFIG_EROFS_FS_DEBUG is not set
CONFIG_EROFS_FS_XATTR=y
CONFIG_EROFS_FS_POSIX_ACL=y
CONFIG_EROFS_FS_SECURITY=y
CONFIG_EROFS_FS_ZIP=y
# CONFIG_EROFS_FS_ZIP_LZMA is not set
CONFIG_EROFS_FS_PCPU_KTHREAD=y
CONFIG_EROFS_FS_PCPU_KTHREAD_HIPRI=y
CONFIG_LZ4_DECOMPRESS=y
CONFIG_LZ4K_DECOMPRESS=y
CONFIG_ZRAM=y
CONFIG_ZRAM_DEF_COMP_LZ4KD=y
CONFIG_ZRAM_DEF_COMP="lz4kd"
CONFIG_ZRAM_WRITEBACK=y
CONFIG_ZRAM_TRACK_ENTRY_ACTIME=y
CONFIG_ZRAM_MULTI_COMP=y
CONFIG_CRYPTO_LZ4=y
CONFIG_CRYPTO_LZ4K=y
CONFIG_CRYPTO_LZ4KD=y
CONFIG_CRYPTO_LZ4HC=y
CONFIG_CRYPTO_ZSTD=y
```

判读：

- **`EROFS_FS_ZIP=y` 且唯一的解压器是 LZ4**（LZMA 未编）⇒ 本组改动的
  `decompressor.c`（LZ4）就是真机在跑的那一条；`decompressor_lzma.c` 在真机上是死代码，
  但两条都改是有意的 —— 它们在上游是同一处语义，分开改会让 LZMA 构建走上「分配必成功」的
  旧行为，且树级审计要求两处一致（见 `tests/implementation_audit.py`）。
- **`EROFS_FS_PCPU_KTHREAD=y` + `_HIPRI=y`** ⇒ 解压跑在 per-CPU 的 RT 优先级 kthread 上。
  注意这**不是**上游 `cf7f2732b4b8`（「per-cpu kthreads 打开时默认开 HIPRI」）——后者只是
  把 Kconfig 的默认值改成 y，而本 ROM 的 config 已经显式选了 HIPRI，所以该提交的内容在本
  真机上已被 config 层覆盖，**无需移植**。
- **滑窗与 pcluster 尺寸**：superblock 的 `u1.lz4_max_distance = 0xFFFF = 65535`
  ⇒ **64 KiB 滑窗**；`feature_incompat` 无 `BIG_PCLUSTER`(0x2) ⇒ **4 KiB pcluster**。
  这正落在上游 benchmark 的「4k pclusters / 64k sliding window」那一列
  （3364 → 2684 ms，−20.2%），不是 16k 窗那一列（−22.6%）。收益主张按前者。
- **`feature_incompat = 0x00000001`**（只有 `EROFS_FEATURE_INCOMPAT_LZ4_0PADDING`），
  **无 `COMPR_CFGS`(0x2)**。这一点与「inode 用 `FLAT_COMPRESSION`（3）而非
  `_LEGACY`（1）」在格式上看似矛盾，但**不影响本批结论**：目录树走查取到的是被目录项真实
  引用的文件，datalayout 判定明确。记录在此供后续 erofs 工作注意：`feature_incompat`
  这一位在这台设备的镜像上不能单独用来判定「是否压缩」。

## zram 侧事实（与原问题的「erofs → zram」部分对照）

主算法 `lz4kd`（厂商编解码器，`CONFIG_ZRAM_DEF_COMP="lz4kd"`，`CONFIG_CRYPTO_LZ4KD=y`），
二级 `zstd`（`CONFIG_CRYPTO_ZSTD=y`）。**上游 `lib/lz4` 在 `v5.15..v7.2` 无任何性能提交**，
且主算法根本不是 `lib/lz4` 的 `lz4` —— 这两条合起来就是「erofs 补丁无法提升 zram 压缩性能」
的第 5 条证据（前四条见 `CHANGELOG.md#batch-40`）。

## 复现方法

```bash
adb mdns services | grep _adb-tls-connect     # 取 192.168.31.20:<port>
adb connect 192.168.31.20:<port>
D=192.168.31.20:<port>
adb -s $D shell 'cat /proc/self/mountinfo' | grep -i erofs
adb -s $D shell 'su -c "zcat /proc/config.gz | grep -E \"CONFIG_EROFS|CONFIG_ZRAM|CONFIG_LZ4|CONFIG_ZSTD\""'
# 逐 inode 判定：拉 16 MB 头部后用下面的脚本走目录树
adb -s $D exec-out su -c 'dd if=/dev/block/dm-3 bs=4096 count=4096 2>/dev/null' > dm3_head.img
python3 erofs_probe_inode.py dm3_head.img
```

```python
# erofs_probe_inode.py -- 5.15 的 erofs on-disk 格式（12 字节 dirent、12 字节 xattr header）
import struct, sys
FT = {0:"?",1:"reg",2:"dir",3:"chr",4:"blk",5:"fifo",6:"sock",7:"lnk"}
LAY = {0:"FLAT_PLAIN",1:"COMPRESSION_LEGACY",2:"FLAT_INLINE",3:"COMPRESSION"}
d = open(sys.argv[1], "rb").read()
SB = 1024
assert struct.unpack_from("<I", d, SB)[0] == 0xE0F5E1E2, "not erofs"
bsz = 1 << d[SB+12]
root = struct.unpack_from("<H", d, SB+14)[0]
print(f"blkszbits={d[SB+12]} root_nid={root} feature_incompat=0x{struct.unpack_from('<I',d,SB+80)[0]:x}")

def inode(nid):
    o = nid * 32
    ifmt, xic, mode, nlink, size, _r, iu = struct.unpack_from("<HHHHIII", d, o)
    return dict(ifmt=ifmt, icount=xic, size=size, i_u=iu,
                lay=(ifmt >> 1) & 3, isz=64 if ifmt & 1 else 32)

def dirblob(nid):
    i = inode(nid)
    xs = 0 if not i["icount"] else 12 + 4 * (i["icount"] - 1)   # NOT 4*(icount-1)
    o = nid * 32 + i["isz"] + xs
    return d[o:o + i["size"]]

def walk(nid, depth=0):
    blob = dirblob(nid)
    maxsize = struct.unpack_from("<H", blob, 8)[0]
    n = maxsize // 12                                          # 12-byte dirent, NOT 16
    for k in range(n):
        enid, noff, ft = struct.unpack_from("<QHB", blob, 12 * k)
        nxt = struct.unpack_from("<H", blob, 12 * (k + 1) + 8)[0] if k + 1 < n else None
        name = blob[noff:noff + (nxt - noff)].decode() if nxt and nxt > noff else "?"
        if depth == 0:
            i = inode(enid)
            print(f"  nid={enid:<5d} {FT.get(ft,'?'):>4s} {LAY[i['lay']]:20s} "
                  f"size={i['size']:<9d} {name}")
        elif FT.get(ft) == "reg":
            i = inode(enid)
            print(f"  reg nid={enid:<5d} size={i['size']:<9d} layout={LAY[i['lay']]:20s} "
                  f"compressed_blocks={i['i_u'] if i['lay'] in (1,3) else '-'}")
walk(root, 0)
```

对本设备 `dm-3`（`/vendor`）跑出的关键行：

```
  nid=110  reg  COMPRESSION          size=16314     build.prop
```
