import re
import bml
import xml.etree.ElementTree as ET

def html_bidtable(et_element, children, indent_level_is_even):
    # `indent_level_is_even` let's us alternately cycle the css class for every indent of the bid table
    div_class = "start" if indent_level_is_even else "startOddIndent"
    if len(children) > 0:
        ul = ET.SubElement(et_element, 'ul')
        for c in children:
            li = ET.SubElement(ul, 'li')
            div = ET.SubElement(li, 'div')
            div.attrib['class'] = div_class
            desc_rows = c.desc.split('\\n')
            bid = re.sub(r'^P$', 'Pass', c.bid)
            bid = re.sub(r'^R$', 'Rdbl', bid)
            bid = re.sub(r'^D$', 'Dbl', bid)
            div.text = bid
            div.tail = desc_rows[0]
            desc_rows = desc_rows[1:]
            for dr in desc_rows:
                rowli = ET.SubElement(ul, 'li')
                rowdiv = ET.SubElement(rowli, 'div')
                rowdiv.attrib['class'] = div_class
                rowdiv.text = ' '
                rowdiv.tail = dr
            html_bidtable(li, c.children, not indent_level_is_even)

def html_replace_suits(matchobj):
    text = matchobj.group(0)
    text = text.replace('C', '<span class="ccolor">&clubs;</span>')
    text = text.replace('D', '<span class="dcolor">&diams;</span>')
    text = text.replace('H', '<span class="hcolor">&hearts;</span>')
    text = text.replace('S', '<span class="scolor">&spades;</span>')
    text = text.replace('N', 'NT')
    return text

def replace_strong(matchobj):
    return '<strong>' + matchobj.group(1) + '</strong>'

def replace_italics(matchobj):
    return '<em>' + matchobj.group(1) + '</em>'

def replace_truetype(matchobj):
    return '<code>' + matchobj.group(1) + '</code>'

def replace_markdown_link(matchobj):
    link_text = matchobj.group(1).strip()
    fragment_link_target = matchobj.group(2).strip()
    norm_target = normalise_header_id(fragment_link_target)
    return '<a href="{target}"/>{text}</a>'.format(target=norm_target, text=link_text)

def normalise_header_id(text):
    # don't want spaces
    # don't want text that can be replaced with html suit special symbols
    def escape_suits(match):
        suit_text = match.group(0)
        return suit_text.lower()

    norm = text.replace(" ", "_").strip()
    norm = re.sub(r"\d[CDHS]", escape_suits, norm)
    return norm

def to_html(content):
    html = ET.Element('html')
    head = ET.SubElement(html, 'head')
    link = ET.SubElement(head, 'link')
    link.attrib['rel'] = 'stylesheet'
    link.attrib['type'] = 'text/css'
    link.attrib['href'] = 'bml.css'

    open_sans_font_link = ET.SubElement(head, 'link')
    open_sans_font_link.attrib['href'] = "https://fonts.googleapis.com/css?family=Open Sans"
    open_sans_font_link.attrib['rel'] = "stylesheet"

    body = ET.SubElement(html, 'body')
    body.attrib['class'] = "content"

    for c in content:
        content_type, text = c
        if content_type == bml.ContentType.PARAGRAPH:
            element = ET.SubElement(body, 'p')
            element.text = text
        elif content_type == bml.ContentType.BIDTABLE:
            if not text.export:
                continue
            element = ET.SubElement(body, 'div')
            element.attrib['class'] = 'bidtable'
            html_bidtable(element, text.children, indent_level_is_even=True)
        elif content_type == bml.ContentType.H1:
            # The id="text" part for the header elements unfortunately could be regex replaced
            # later, so normalise it so that cannot happen given known suit replacements
            element = ET.SubElement(body, 'h1', {"id": normalise_header_id(text)})
            element.text = text
        elif content_type == bml.ContentType.H2:
            element = ET.SubElement(body, 'h2', {"id": normalise_header_id(text)})
            element.text = text
        elif content_type == bml.ContentType.H3:
            element = ET.SubElement(body, 'h3', {"id": normalise_header_id(text)})
            element.text = text
        elif content_type == bml.ContentType.H4:
            element = ET.SubElement(body, 'h4', {"id": normalise_header_id(text)})
            element.text = text
        elif content_type == bml.ContentType.LIST:
            element = ET.SubElement(body, 'ul')
            for l in text:
                li = ET.SubElement(element, 'li')
                li.text = l
        elif content_type == bml.ContentType.ENUM:
            element = ET.SubElement(body, 'ol')
            for l in text:
                li = ET.SubElement(element, 'li')
                li.text = l

    title = ET.SubElement(head, 'title')
    title.text = bml.meta['TITLE']
    htmlstring = str(ET.tostring(html), 'UTF8')

    suit_replace_regex = re.compile(r"""
        \d  # a number
        (
            [CDHS]  # CDHS to replace with spans, but will have to check it's not in [] or () somehow
            (?![^\(]*\)) # but was not inside parentheses, the link target syntax
            | # or an N (but not NT which will become NT after replacement)
            N(?!T)
        )+ # 1+ suit or N symbols to replace
        """, re.VERBOSE)
    htmlstring = re.sub(suit_replace_regex, html_replace_suits, htmlstring)

    htmlstring = re.sub(r'(?<=\s)\*(\S[^*<>]*)\*', replace_strong, htmlstring, flags=re.DOTALL)
    htmlstring = re.sub(r'(?<=\s)/(\S[^/<>]*)/', replace_italics, htmlstring, flags=re.DOTALL)
    htmlstring = re.sub(r'(?<=\s)=(\S[^=<>]*)=', replace_truetype, htmlstring, flags=re.DOTALL)

    # Replaces !c!d!h!s with suit symbols
    htmlstring = htmlstring.replace('!c', '<span class="ccolor">&clubs;</span>')
    htmlstring = htmlstring.replace('!d', '<span class="dcolor">&diams;</span>')
    htmlstring = htmlstring.replace('!h', '<span class="hcolor">&hearts;</span>')
    htmlstring = htmlstring.replace('!s', '<span class="scolor">&spades;</span>')

    # Replace "long dashes"
    htmlstring = htmlstring.replace('---', '&mdash;')
    htmlstring = htmlstring.replace('--', '&ndash;')

    # (?!...) negative lookahead assert
    # [link text](target url / relative fragment url)
    # link text may have had text replaced to use suit symbols etc.
    # The target () needs to match the target header "id", so must not be substituted
    # target url avoids text replacement by normalising that text, normalised in the
    # same way as a header id
    htmlstring = re.sub(r'\[(.*?)]\((.+?)\)', replace_markdown_link, htmlstring)

    return htmlstring

if __name__ == '__main__':
    import sys
    import os

    outputfile = ''
    if len(sys.argv) < 2:
        print("What's the name of the file you want to convert?")
        outputfile = input()
        if not os.path.exists(outputfile):
            sys.exit('ERROR: File %s was not found!' % outputfile)
        bml.content_from_file(outputfile)
        outputfile = outputfile.split('.')[0]
    else:
        if not os.path.exists(sys.argv[1]):
            sys.exit('ERROR: File %s was not found!' % sys.argv[1])

        bml.content_from_file(sys.argv[1])
        outputfile = os.path.basename(sys.argv[1]).split('.')[0]

    h = to_html(bml.content)
    f = open(outputfile + '.html', 'w')
    f.write(h)
    f.close()
