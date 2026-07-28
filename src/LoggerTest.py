import sys
# Prevent Python from generating .pyc files (compiled bytecode files)
sys.dont_write_bytecode = True

from Logger import Logger 

l = Logger()
l.log("This is a Test.")
