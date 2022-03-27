#!/usr/bin/env /bin/bash
mkdir -p ../term2-calc1-tickets
for i in {01..59} ; do
    grep -Phazo "%BEGIN TICKET $i([\s\S]+)(?<=%END TICKET $i)" ??-*.tex > "../term2-calc1-tickets/ticket$i.tex"
done

