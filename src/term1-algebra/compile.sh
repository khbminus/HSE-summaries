rm *.{toc,out,aux,log}
cat 01-number.tex 02-number.tex 03-rings.tex 04-crypto.tex | pcre2grep -M -e "\\\\begin{(definition|theorem|lemma|consequence|statement|property)}[\\w\\W]+?\\\\end{\1}" > definitions.tex
xelatex -interaction=nonstopmode -halt-on-error definitions1.tex definitions1.tex
rm *.{toc,out,aux,log}
mv definitions1.pdf ../../pdf/term1-algebra-def.pdf
