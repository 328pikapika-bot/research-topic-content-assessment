"""Executed by the service Writer's Python/UNO runtime in an isolated process."""
import sys
import time
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue


def prop(name, value):
    item = PropertyValue()
    item.Name, item.Value = name, value
    return item


def main():
    port, folder = int(sys.argv[1]), Path(sys.argv[2])
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    deadline = time.monotonic() + 70
    while True:
        try:
            context = resolver.resolve(f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError("服务 Writer 启动超时")
            time.sleep(.2)
    desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
    load_options = (prop("Hidden", True), prop("ReadOnly", False), prop("MacroExecutionMode", 0), prop("UpdateDocMode", 0))
    document = desktop.loadComponentFromURL((folder / "report.docx").as_uri(), "_blank", 0, load_options)
    if document is None:
        raise RuntimeError("Writer 无法读取报告")
    try:
        document.refresh()
        indexes = document.getDocumentIndexes()
        for _ in range(2):
            for index in range(indexes.Count):
                indexes.getByIndex(index).update()
            document.refresh()
        document.storeAsURL((folder / "normalized.docx").as_uri(),
                            (prop("FilterName", "Office Open XML Text"), prop("Overwrite", True)))
        # PDF must be exported from the exact persisted DOCX delivered to the
        # user. Reopening catches pagination/font changes introduced by the
        # OOXML save instead of exporting the pre-save in-memory layout.
        document.close(True)
        document = None
        document = desktop.loadComponentFromURL((folder / "normalized.docx").as_uri(), "_blank", 0, load_options)
        if document is None:
            raise RuntimeError("Writer 无法重新读取规范化报告")
        document.refresh()
        document.storeToURL((folder / "report.pdf").as_uri(),
                            (prop("FilterName", "writer_pdf_Export"), prop("Overwrite", True)))
    finally:
        if document is not None:
            document.close(True)
        desktop.terminate()


if __name__ == "__main__":
    main()
