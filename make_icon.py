import math
from PIL import Image, ImageDraw

S = 1024          # рабочий размер (потом уменьшается — сглаживание)
OUT = 256

def star(d, cx, cy, ro, ri, fill):
    pts = []
    for i in range(10):
        a = math.radians(i * 36 - 90)
        r = ro if i % 2 == 0 else ri
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=fill)

def flag_us(size):
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    sh = size / 13
    for i in range(13):
        d.rectangle([0, round(i*sh), size, round((i+1)*sh)],
                    fill=(178, 34, 52) if i % 2 == 0 else (255, 255, 255))
    cw, ch = round(size * 0.46), round(7 * sh)
    d.rectangle([0, 0, cw, ch], fill=(60, 59, 110))
    rows = 9
    ro = size * 0.026
    ri = ro * 0.42
    step = ch / (rows + 1)
    for r in range(rows):
        n = 6 if r % 2 == 0 else 5
        cs = cw / (n + 1)
        for c in range(n):
            star(d, (c+1)*cs, (r+1)*step, ro, ri, (255, 255, 255))
    return img

def flag_ru(size):
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    h = size / 3
    d.rectangle([0, 0, size, h], fill=(255, 255, 255))
    d.rectangle([0, h, size, 2*h], fill=(0, 57, 166))
    d.rectangle([0, 2*h, size, size], fill=(213, 43, 30))
    return img

# США — левый верхний треугольник (виден синий кантон), РФ — правый нижний
us, ru = flag_us(S), flag_ru(S)

tri_us = Image.new("L", (S, S), 0)
ImageDraw.Draw(tri_us).polygon([(0, 0), (S, 0), (0, S)], fill=255)
tri_ru = Image.new("L", (S, S), 0)
ImageDraw.Draw(tri_ru).polygon([(S, 0), (S, S), (0, S)], fill=255)

base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
base.paste(us, mask=tri_us)
base.paste(ru, mask=tri_ru)

# тонкая чёрная косая черта "/"
lw = round(S * 0.022)
ImageDraw.Draw(base).line([(S, 0), (0, S)], fill=(0, 0, 0, 255), width=lw)

# скругление углов + рамка
radius = round(S * 0.125)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S-1, S-1], radius=radius, fill=255)
out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
out.paste(base, mask=mask)
ImageDraw.Draw(out).rounded_rectangle([0, 0, S-1, S-1], radius=radius,
                                      outline=(90, 90, 90, 170), width=round(S*0.012))

final = out.resize((OUT, OUT), Image.LANCZOS)
final.save("icon.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
final.save("icon_preview.png")
print("ok")
