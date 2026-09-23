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
